"""Pipeline orchestration: earcons, follow-up mode, virtual-wake transitions."""

import pytest

from hermes_satellite.config import ConversationConfig
from hermes_satellite.core import pipeline as pipeline_mod
from hermes_satellite.core.events import StateMachine
from hermes_satellite.core.states import State
from hermes_satellite.core.pipeline import Pipeline


@pytest.fixture(autouse=True)
def _no_settle_delay(monkeypatch):
    """The pre-flush settle pause is dead time in tests."""
    monkeypatch.setattr(pipeline_mod, "MIC_SETTLE_S", 0.0)


class FakeWake:
    def __init__(self, results):
        self._r = list(results)
        self.on_exhausted = None  # lets run_forever tests stop the loop
        self.cancelled = 0        # barge listeners cancelled at playback end

    def wait_for_wake(self, is_muted, cancel=None):
        if self._r:
            return self._r.pop(0)
        if cancel is not None:
            cancel.wait(2)  # barge listener: idle until playback ends
            self.cancelled += 1
            return False
        if self.on_exhausted is not None:
            self.on_exhausted()
        return False

    def stop(self):
        pass


class FakeSource:
    def __init__(self, utterances):
        self._u = list(utterances)
        self.onsets = []

    def capture_utterance(self, is_muted, onset_timeout=None, on_frame=None):
        self.onsets.append(onset_timeout)
        audio = self._u.pop(0) if self._u else b""
        if on_frame is not None and audio:
            on_frame(audio)
        return audio


class FakeSTT:
    def __init__(self, texts):
        self._t = list(texts)

    def start_session(self):
        return None

    def transcribe(self, audio):
        return self._t.pop(0) if self._t else ""


class FakeHermes:
    def __init__(self):
        self.calls = []

    def send(self, text, session_key):
        self.calls.append((text, session_key))
        return f"reply to {text}"


class FakeTTS:
    sample_rate = 16000

    def synthesize(self, text):
        return b"\x00\x00"


class RecordingSink:
    def __init__(self):
        self.plays = 0
        self.interrupted = 0

    def play(self, pcm, sample_rate=None, cancel=None):
        if cancel is not None and cancel.is_set():
            self.interrupted += 1
            return
        self.plays += 1


class SpyEarcons:
    def __init__(self):
        self.played = []

    def play(self, cue):
        self.played.append(cue)


def _pipeline(wake, source, texts, conversation=None, flushes=None):
    sm = StateMachine(initial=State.IDLE)
    earcons = SpyEarcons()
    sink = RecordingSink()
    hermes = FakeHermes()
    return Pipeline(
        state_machine=sm,
        wakeword=FakeWake(wake),
        audio_source=source,
        stt=FakeSTT(texts),
        hermes=hermes,
        tts=FakeTTS(),
        audio_sink=sink,
        session_key="dev-key",
        is_muted=lambda: False,
        earcons=earcons,
        conversation=conversation or ConversationConfig(),
        mic_flush=((lambda: flushes.append(1)) if flushes is not None else None),
    ), sm, earcons, sink, hermes


def test_single_turn_no_followup():
    source = FakeSource([b"audio"])
    pipe, sm, earcons, sink, hermes = _pipeline(
        [True], source, ["what time is it"]
    )
    pipe.run_cycle()
    assert hermes.calls == [("what time is it", "dev-key")]
    assert sink.plays == 1
    assert earcons.played == ["wake"]        # chime on wake, none after
    assert source.onsets == [None]           # first turn uses default timeout
    assert sm.state is State.IDLE


def test_wake_but_no_speech_resets():
    pipe, sm, earcons, sink, hermes = _pipeline([True], FakeSource([b""]), [])
    pipe.run_cycle()
    assert hermes.calls == []
    assert earcons.played == ["wake"]        # still chimes — it did wake
    assert sm.state is State.IDLE


def test_followup_continues_then_ends_on_silence():
    source = FakeSource([b"one", b"two", b""])   # 3rd capture: no speech
    flushes = []
    pipe, sm, earcons, sink, hermes = _pipeline(
        [True], source, ["one", "two"],
        conversation=ConversationConfig(follow_up=True, follow_up_seconds=6.0),
        flushes=flushes,
    )
    pipe.run_cycle()
    assert [c[0] for c in hermes.calls] == ["one", "two"]
    assert sink.plays == 2
    # default onset first, follow-up window on the next two capture attempts
    assert source.onsets == [None, 6.0, 6.0]
    # wake chime once, listening chime before each follow-up capture
    assert earcons.played == ["wake", "listening", "listening"]
    # mic flushed after wake and before each follow-up capture
    assert len(flushes) == 3
    assert sm.state is State.IDLE


def test_followup_respects_max_turns():
    # Speech every time; the cap must stop it (no spurious trailing chime).
    source = FakeSource([b"a", b"b", b"c", b"d"])
    pipe, sm, earcons, sink, hermes = _pipeline(
        [True], source, ["a", "b", "c", "d"],
        conversation=ConversationConfig(follow_up=True, max_turns=2),
    )
    pipe.run_cycle()
    assert len(hermes.calls) == 2
    assert earcons.played == ["wake", "listening"]  # not a 2nd listening
    assert sm.state is State.IDLE


def test_error_plays_error_earcon():
    class Boom:
        def transcribe(self, audio):
            raise RuntimeError("stt exploded")

    sm = StateMachine(initial=State.IDLE)
    earcons = SpyEarcons()
    wake = FakeWake([True])
    pipe = Pipeline(
        state_machine=sm, wakeword=wake,
        audio_source=FakeSource([b"audio"]), stt=Boom(),
        hermes=FakeHermes(), tts=FakeTTS(), audio_sink=RecordingSink(),
        session_key="k", is_muted=lambda: False, earcons=earcons,
    )
    wake.on_exhausted = pipe.stop  # end run_forever after the error cycle
    pipe.run_forever()
    assert "error" in earcons.played
    assert sm.state is State.IDLE


# --- streaming replies ----------------------------------------------------

class StreamingHermes(FakeHermes):
    def __init__(self, deltas, fail_after=None):
        super().__init__()
        self._deltas = deltas
        self._fail_after = fail_after
        self.stream_calls = 0

    def send_stream(self, text, session_key):
        self.stream_calls += 1
        self.calls.append((text, session_key))

        def gen():
            for i, d in enumerate(self._deltas):
                if self._fail_after is not None and i >= self._fail_after:
                    raise RuntimeError("stream broke")
                yield d
        return gen()


class CountingTTS(FakeTTS):
    def __init__(self):
        self.synthesized = []

    def synthesize(self, text):
        self.synthesized.append(text)
        return b"\x00\x00"


def _streaming_pipeline(hermes, tts=None):
    sm = StateMachine(initial=State.IDLE)
    sink = RecordingSink()
    pipe = Pipeline(
        state_machine=sm, wakeword=FakeWake([True]),
        audio_source=FakeSource([b"audio"]), stt=FakeSTT(["question"]),
        hermes=hermes, tts=tts or CountingTTS(), audio_sink=sink,
        session_key="k", is_muted=lambda: False, earcons=SpyEarcons(),
        stream_replies=True,
    )
    return pipe, sm, sink


def test_streaming_speaks_sentence_by_sentence():
    hermes = StreamingHermes(
        ["It is currently 2.47 PM Pacific Time. ",
         "Tomorrow looks sunny and pleasant all day."])
    tts = CountingTTS()
    pipe, sm, sink = _streaming_pipeline(hermes, tts)
    pipe.run_cycle()
    assert hermes.stream_calls == 1
    assert len(tts.synthesized) == 2          # one synth per sentence
    assert sink.plays == 2
    assert sm.state is State.IDLE


def test_stream_not_started_falls_back_to_blocking():
    """Fallback is allowed ONLY when no turn started on Hermes."""
    from hermes_satellite.hermes.base import HermesStreamNotStarted

    class NoStream(FakeHermes):
        def send_stream(self, text, session_key):
            raise HermesStreamNotStarted("HTTP 400: streaming unsupported")

    hermes = NoStream()
    pipe, sm, sink = _streaming_pipeline(hermes)
    pipe.run_cycle()
    assert hermes.calls  # blocking send() was used
    assert sink.plays == 1
    assert sm.state is State.IDLE


def test_stream_timeout_does_not_resend(caplog):
    """Regression (field incident): a quiet-stream timeout used to fall back
    to a blocking send of the SAME message — Hermes already had it, and the
    duplicate tripped busy_input_mode:interrupt, killing the in-flight turn.
    Failures after the request was delivered must NOT re-send."""
    from hermes_satellite.hermes.base import HermesError

    class QuietStream(FakeHermes):
        def __init__(self):
            super().__init__()
            self.blocking_sends = 0

        def send_stream(self, text, session_key):
            raise HermesError("Hermes stream went quiet for 300s")

        def send(self, text, session_key):
            self.blocking_sends += 1
            return super().send(text, session_key)

    hermes = QuietStream()
    pipe, sm, sink = _streaming_pipeline(hermes)
    import pytest as _p
    with _p.raises(HermesError, match="quiet"):
        pipe.run_cycle()
    assert hermes.blocking_sends == 0   # the message was never re-sent
    assert sink.plays == 0


def test_streaming_midway_failure_errors_after_partial_speech():
    hermes = StreamingHermes(
        ["First sentence spoken fully and completely. ",
         "Second sentence never finishes"], fail_after=1)
    tts = CountingTTS()
    pipe, sm, sink = _streaming_pipeline(hermes, tts)
    pipe.run_forever_once = None
    import pytest as _p
    with _p.raises(RuntimeError, match="stream broke"):
        pipe.run_cycle()
    assert sink.plays >= 1  # partial reply was heard


def test_stream_disabled_uses_blocking_even_if_supported():
    hermes = StreamingHermes(["Whole reply in one go, spoken at once."])
    sm = StateMachine(initial=State.IDLE)
    pipe = Pipeline(
        state_machine=sm, wakeword=FakeWake([True]),
        audio_source=FakeSource([b"audio"]), stt=FakeSTT(["q"]),
        hermes=hermes, tts=CountingTTS(), audio_sink=RecordingSink(),
        session_key="k", is_muted=lambda: False,
        stream_replies=False,
    )
    pipe.run_cycle()
    assert hermes.stream_calls == 0
    assert hermes.calls  # blocking path


# --- barge-in ---------------------------------------------------------------

def test_barge_in_interrupts_and_starts_new_turn():
    # Wake #1 starts the cycle; the barge listener consumes wake #2 during
    # the first reply's playback; the second turn's listener finds no more
    # wakes and just waits for cancellation.
    source = FakeSource([b"one", b"two"])
    pipe, sm, earcons, sink, hermes = _pipeline(
        [True, True], source, ["one", "two"],
        conversation=ConversationConfig(barge_in=True),
    )
    pipe.run_cycle()
    assert [c[0] for c in hermes.calls] == ["one", "two"]
    # both turns used the default onset timeout (a barge is a fresh wake,
    # not a follow-up window)
    assert source.onsets == [None, None]
    # wake chime for the original wake and again for the barge
    assert earcons.played == ["wake", "wake"]
    assert sm.state is State.IDLE
    # the second turn's listener was cancelled when its playback finished
    assert pipe.wakeword.cancelled == 1


def test_no_barge_when_disabled():
    pipe, sm, earcons, sink, hermes = _pipeline(
        [True, True], FakeSource([b"one", b"two"]), ["one", "two"],
    )
    pipe.run_cycle()
    # second wake result untouched: no barge listener ever ran
    assert len(hermes.calls) == 1
    assert pipe.wakeword._r == [True]
    assert sm.state is State.IDLE


def test_barge_in_streaming_reply():
    hermes = StreamingHermes(
        ["First sentence, quite long and complete. ",
         "Second sentence never gets played."])
    sm = StateMachine(initial=State.IDLE)
    sink = RecordingSink()
    source = FakeSource([b"q1", b"q2"])
    pipe = Pipeline(
        state_machine=sm, wakeword=FakeWake([True, True]),
        audio_source=source, stt=FakeSTT(["q1", "q2"]),
        hermes=hermes, tts=CountingTTS(), audio_sink=sink,
        session_key="k", is_muted=lambda: False, earcons=SpyEarcons(),
        stream_replies=True,
        conversation=ConversationConfig(barge_in=True),
    )
    pipe.run_cycle()
    # both questions reached Hermes; no hang from the synth-ahead producer
    assert [c[0] for c in hermes.calls] == ["q1", "q2"]
    assert sm.state is State.IDLE


def test_stop_command_ends_followup_without_hermes():
    source = FakeSource([b"one", b"stop-audio"])
    pipe, sm, earcons, sink, hermes = _pipeline(
        [True], source, ["one", "Stop."],
        conversation=ConversationConfig(follow_up=True),
    )
    pipe.run_cycle()
    assert [c[0] for c in hermes.calls] == ["one"]  # "Stop." never sent
    assert earcons.played == ["wake", "listening", "done"]
    assert sm.state is State.IDLE


def test_dedicated_stop_model_barge_goes_idle():
    """With conversation.barge_model_path wired (barge_wakeword), a barge
    means STOP: playback aborts and the cycle ends — no new capture."""
    source = FakeSource([b"one"])
    sm = StateMachine(initial=State.IDLE)
    earcons = SpyEarcons()
    sink = RecordingSink()
    hermes = FakeHermes()
    stop_detector = FakeWake([True])   # fires immediately during playback
    pipe = Pipeline(
        state_machine=sm, wakeword=FakeWake([True]),
        audio_source=source, stt=FakeSTT(["one"]),
        hermes=hermes, tts=FakeTTS(), audio_sink=sink,
        session_key="k", is_muted=lambda: False, earcons=earcons,
        conversation=ConversationConfig(barge_in=True),
        barge_wakeword=stop_detector,
    )
    pipe.run_cycle()
    assert len(hermes.calls) == 1
    assert len(source.onsets) == 1           # no post-barge capture
    assert earcons.played == ["wake", "done"]
    assert sm.state is State.IDLE


def test_turn_timing_logged(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="hermes_satellite.core.pipeline")
    source = FakeSource([b"audio"])
    pipe, sm, earcons, sink, hermes = _pipeline([True], source, ["question"])
    pipe.run_cycle()
    timing_lines = [r.message for r in caplog.records
                    if r.message.startswith("turn timing:")]
    assert len(timing_lines) == 1
    line = timing_lines[0]
    for stage in ("capture", "stt", "first-reply", "first-audio", "total"):
        assert stage in line, line


# --- streaming STT sessions -------------------------------------------------

class SessionSTT(FakeSTT):
    """STT with capture-time sessions; records fed audio and lifecycle."""

    class Session:
        def __init__(self, outer):
            self.outer = outer
            self.fed = []
            self.finished = False
            self.aborted = False

        def feed(self, pcm):
            self.fed.append(pcm)

        def finish(self):
            self.finished = True
            return self.outer._t.pop(0) if self.outer._t else ""

        def abort(self):
            self.aborted = True

    def __init__(self, texts):
        super().__init__(texts)
        self.sessions = []
        self.batch_calls = 0

    def start_session(self):
        s = self.Session(self)
        self.sessions.append(s)
        return s

    def transcribe(self, audio):
        self.batch_calls += 1
        return super().transcribe(audio)


def test_stt_session_used_for_capture():
    source = FakeSource([b"spoken-audio"])
    stt = SessionSTT(["what time is it"])
    sm = StateMachine(initial=State.IDLE)
    hermes = FakeHermes()
    pipe = Pipeline(
        state_machine=sm, wakeword=FakeWake([True]), audio_source=source,
        stt=stt, hermes=hermes, tts=FakeTTS(), audio_sink=RecordingSink(),
        session_key="k", is_muted=lambda: False,
    )
    pipe.run_cycle()
    assert len(stt.sessions) == 1
    session = stt.sessions[0]
    assert session.fed == [b"spoken-audio"]   # capture fed the session
    assert session.finished is True
    assert stt.batch_calls == 0               # batch path never used
    assert hermes.calls == [("what time is it", "k")]
    assert sm.state is State.IDLE


def test_stt_session_aborted_when_no_speech():
    source = FakeSource([b""])
    stt = SessionSTT([])
    sm = StateMachine(initial=State.IDLE)
    pipe = Pipeline(
        state_machine=sm, wakeword=FakeWake([True]), audio_source=source,
        stt=stt, hermes=FakeHermes(), tts=FakeTTS(),
        audio_sink=RecordingSink(), session_key="k", is_muted=lambda: False,
    )
    pipe.run_cycle()
    assert stt.sessions[0].aborted is True
    assert stt.sessions[0].finished is False
    assert sm.state is State.IDLE
