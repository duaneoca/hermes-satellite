"""VAD-gated capture logic, tested with a fake mic and fake VAD."""

from hermes_satellite.audio.alsa_backend import FRAME_MS, AlsaAudioSource
from hermes_satellite.config import AudioConfig

SAMPLES_PER_FRAME = 16000 * FRAME_MS // 1000
FRAME = b"\x01\x00" * SAMPLES_PER_FRAME  # one 30 ms frame of int16


class FakeMic:
    """Returns FRAME forever; capture logic decides when to stop reading."""

    def __init__(self):
        self.started = False
        self.reads = 0

    def start(self):
        self.started = True

    def read(self, num_frames):
        self.reads += 1
        return FRAME


class FakeVAD:
    """is_speech follows a scripted sequence, then repeats the last value."""

    def __init__(self, script):
        self._script = list(script)

    def is_speech(self, frame):
        if len(self._script) > 1:
            return self._script.pop(0)
        return self._script[0]


def _source(script, **cfg_kw):
    config = AudioConfig(**cfg_kw)
    mic = FakeMic()
    return AlsaAudioSource(config, mic=mic, vad=FakeVAD(script)), mic


def test_capture_onset_speech_then_silence():
    # 2 silent frames, 10 speech frames, then trailing silence.
    src, mic = _source([False, False] + [True] * 10 + [False], silence_ms=90)
    audio = src.capture_utterance(lambda: False)
    # pre-roll (2 silent) + 10 speech + 3 trailing silent (90/30) frames
    expected_frames = 2 + 10 + 3
    assert len(audio) == expected_frames * len(FRAME)
    assert mic.started


def test_capture_returns_empty_when_muted():
    src, mic = _source([True])
    assert src.capture_utterance(lambda: True) == b""
    assert mic.reads == 0


def test_capture_speech_timeout_returns_empty():
    src, _ = _source([False], speech_timeout_seconds=0.0)
    assert src.capture_utterance(lambda: False) == b""


def test_capture_hard_cap(monkeypatch):
    # Speech never ends; the max_record_seconds cap must stop the loop.
    src, mic = _source([False, True], max_record_seconds=0.0)
    audio = src.capture_utterance(lambda: False)
    # phase 2 exits immediately at the cap; the onset frames survive via
    # the pre-roll: 1 silent + 3 speech (onset debounce)
    assert len(audio) == 4 * len(FRAME)


def test_single_frame_click_does_not_trigger_onset():
    """Regression: the WM8960's output-stage pop reads as one loud 'speech'
    frame to webrtcvad, which used to open recording instantly — follow-up
    windows ended after ~silence_ms instead of staying open."""
    src, _ = _source([False, True, False], speech_timeout_seconds=0.2)
    assert src.capture_utterance(lambda: False) == b""


def test_two_frame_click_does_not_trigger_onset():
    src, _ = _source([False, True, True, False], speech_timeout_seconds=0.2)
    assert src.capture_utterance(lambda: False) == b""


def test_followup_onset_timeout_overrides_default():
    # onset_timeout=0 must beat a long speech_timeout_seconds
    src, _ = _source([False], speech_timeout_seconds=60.0)
    assert src.capture_utterance(lambda: False, onset_timeout=0.0) == b""


def test_mute_mid_capture_stops_recording():
    calls = {"n": 0}

    def muted():
        calls["n"] += 1
        return calls["n"] > 3  # unmuted for onset, then muted

    src, _ = _source([True])  # immediate speech
    audio = src.capture_utterance(muted)
    assert audio  # what was captured before the mute is returned


def test_sink_play_blocks_for_audio_duration(monkeypatch):
    """Regression: write() returns once frames are buffered and closing an
    active stream discards the rest — play() returned while sound was still
    leaving the speaker, so the mic flush ran too early and the capture VAD
    opened on our own earcon (follow-up windows died after ~1 s)."""
    import sys
    import time
    import types as t

    class FakeOut:
        latency = 0.01

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def write(self, pcm):  # instant, like a large host buffer
            pass

    sd = t.ModuleType("sounddevice")
    sd.RawOutputStream = lambda **kw: FakeOut()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)

    from hermes_satellite.audio.alsa_backend import AlsaAudioSink
    from hermes_satellite.config import AudioConfig

    sink = AlsaAudioSink(AudioConfig())
    pcm = b"\x00\x00" * 1600  # 100 ms at 16 kHz
    started = time.monotonic()
    sink.play(pcm, 16000)
    assert time.monotonic() - started >= 0.1


def test_sink_play_cancel_aborts_early(monkeypatch):
    """A set cancel event must cut playback off well before the clip ends."""
    import sys
    import threading
    import time
    import types as t

    aborted = []

    class FakeOut:
        latency = 0.01

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def write(self, pcm):
            pass

        def abort(self):
            aborted.append(True)

    sd = t.ModuleType("sounddevice")
    sd.RawOutputStream = lambda **kw: FakeOut()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)

    from hermes_satellite.audio.alsa_backend import AlsaAudioSink
    from hermes_satellite.config import AudioConfig

    sink = AlsaAudioSink(AudioConfig())
    cancel = threading.Event()
    cancel.set()
    pcm = b"\x00\x00" * 32000  # 2 s at 16 kHz
    started = time.monotonic()
    sink.play(pcm, 16000, cancel=cancel)
    assert time.monotonic() - started < 0.5
    assert aborted


def test_on_frame_receives_preroll_and_speech_in_order():
    """Streaming STT contract: every frame that lands in the returned audio
    is also delivered to on_frame, in order — pre-roll included."""
    src, _ = _source([False, False] + [True] * 10 + [False], silence_ms=90)
    frames = []
    audio = src.capture_utterance(lambda: False, on_frame=frames.append)
    assert b"".join(frames) == audio
    assert len(frames) == len(audio) // len(FRAME)


def test_on_frame_not_called_when_no_speech():
    src, _ = _source([False], speech_timeout_seconds=0.0)
    frames = []
    assert src.capture_utterance(lambda: False, on_frame=frames.append) == b""
    assert frames == []


def test_false_onset_rearms_and_captures_real_speech():
    """Regression (field, silence_ms=200): a click that slips past the onset
    debounce used to end capture before the user could start talking. Now a
    trailing-silence ending with <MIN_SPEECH_MS of voiced audio re-arms
    within the original onset window."""
    # click: 3 speech frames, then 3 silent (silence_ms=90 ends attempt 1
    # with only 90ms of speech) -> re-arm -> real utterance: 10 speech.
    script = [True, True, True] + [False] * 4 + [True] * 10 + [False]
    src, _ = _source(script, silence_ms=90)
    audio = src.capture_utterance(lambda: False)
    # the returned audio is the SECOND attempt (real speech), not the click:
    # re-armed pre-roll (silent frames) + 10 speech + 3 trailing
    assert len(audio) >= 10 * len(FRAME)
    n_frames = len(audio) // len(FRAME)
    assert n_frames <= 3 + 10 + 3  # no click content beyond the pre-roll


def test_false_onset_only_still_times_out():
    """A click with no real speech afterwards must still give up at the
    original onset deadline (no infinite re-arming)."""
    script = [True, True, True, False]
    src, _ = _source(script, silence_ms=60, speech_timeout_seconds=0.25)
    assert src.capture_utterance(lambda: False) == b""
