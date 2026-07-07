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
    # 2 silent frames, 5 speech frames, then silence until the 800 ms timeout.
    src, mic = _source([False, False] + [True] * 5 + [False], silence_ms=90)
    audio = src.capture_utterance(lambda: False)
    # pre-roll (2 silent) + onset + 4 speech + 3 trailing silent (90/30) frames
    expected_frames = 2 + 5 + 3
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
    # onset frame only: phase 2 loop exits immediately at the cap.
    assert len(audio) == 2 * len(FRAME)  # 1 pre-roll + onset frame


def test_mute_mid_capture_stops_recording():
    calls = {"n": 0}

    def muted():
        calls["n"] += 1
        return calls["n"] > 3  # unmuted for onset, then muted

    src, _ = _source([True])  # immediate speech
    audio = src.capture_utterance(muted)
    assert audio  # what was captured before the mute is returned
