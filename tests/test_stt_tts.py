"""Moonshine and Piper backends, tested with fake library modules."""

import sys
import types
from pathlib import Path

import pytest

from hermes_satellite.config import STTConfig, TTSConfig
from hermes_satellite.stt.moonshine_backend import MoonshineSTT
from hermes_satellite.tts.piper_backend import PiperTTS


# ---------------------------------------------------------------- moonshine

class FakeTranscriber:
    def __init__(self, model_path, model_arch=None):
        self.model_path = model_path
        self.model_arch = model_arch
        self.received = None

    def transcribe_without_streaming(self, samples, sample_rate=16000):
        self.received = (samples, sample_rate)
        line1 = types.SimpleNamespace(text="hello")
        line2 = types.SimpleNamespace(text="world")
        return types.SimpleNamespace(lines=[line1, line2])


@pytest.fixture
def fake_moonshine(monkeypatch, tmp_path):
    module = types.ModuleType("moonshine_voice")
    module.ModelArch = types.SimpleNamespace(TINY="tiny-arch", BASE="base-arch")
    model_dir = tmp_path / "base-en"
    model_dir.mkdir()

    def get_model_for_language(language, arch):
        module.requested = (language, arch)
        return str(model_dir), arch  # absolute path, like the real library

    module.get_model_for_language = get_model_for_language
    module.get_model_path = lambda name: Path(name)
    module.Transcriber = FakeTranscriber
    monkeypatch.setitem(sys.modules, "moonshine_voice", module)
    return module


def test_moonshine_transcribes_and_joins_lines(fake_moonshine):
    stt = MoonshineSTT(STTConfig(model="moonshine/base"))
    # Two int16 samples: 16384 (-> 0.5) and -32768 (-> -1.0).
    audio = (16384).to_bytes(2, "little", signed=True) + (-32768).to_bytes(2, "little", signed=True)
    assert stt.transcribe(audio) == "hello world"
    samples, rate = stt._transcriber.received
    assert rate == 16000
    assert samples[0] == pytest.approx(0.5)
    assert samples[1] == pytest.approx(-1.0)


def test_moonshine_selects_arch_and_language(fake_moonshine):
    stt = MoonshineSTT(STTConfig(model="moonshine/tiny", language="en"))
    stt.transcribe(b"\x00\x00")
    assert fake_moonshine.requested == ("en", "tiny-arch")


def test_moonshine_unknown_model_raises(fake_moonshine):
    stt = MoonshineSTT(STTConfig(model="moonshine/enormous"))
    with pytest.raises(ValueError, match="enormous"):
        stt.transcribe(b"\x00\x00")


def test_moonshine_loads_model_once(fake_moonshine):
    stt = MoonshineSTT(STTConfig())
    stt.transcribe(b"\x00\x00")
    first = stt._transcriber
    stt.transcribe(b"\x00\x00")
    assert stt._transcriber is first


# -------------------------------------------------------------------- piper

class FakeVoiceClassic:
    config = types.SimpleNamespace(sample_rate=22050)

    @classmethod
    def load(cls, path):
        inst = cls()
        inst.path = path
        return inst

    def synthesize_stream_raw(self, text):
        yield b"\x01\x02"
        yield b"\x03\x04"


class FakeChunk:
    def __init__(self, data):
        self.audio_int16_bytes = data
        self.sample_rate = 22050


class FakeVoiceCurrent:
    @classmethod
    def load(cls, path):
        return cls()

    def synthesize(self, text):
        yield FakeChunk(b"\xaa\xbb")
        yield FakeChunk(b"\xcc\xdd")


def _install_piper(monkeypatch, voice_cls):
    piper_pkg = types.ModuleType("piper")
    voice_mod = types.ModuleType("piper.voice")
    voice_mod.PiperVoice = voice_cls
    piper_pkg.voice = voice_mod
    piper_pkg.PiperVoice = voice_cls
    monkeypatch.setitem(sys.modules, "piper", piper_pkg)
    monkeypatch.setitem(sys.modules, "piper.voice", voice_mod)


def test_piper_classic_api(monkeypatch):
    _install_piper(monkeypatch, FakeVoiceClassic)
    tts = PiperTTS(TTSConfig(voice_path="/v.onnx"))
    pcm = tts.synthesize("hi")
    assert pcm == b"\x01\x02\x03\x04"
    assert tts.sample_rate == 22050  # from voice.config


def test_piper_current_chunk_api(monkeypatch):
    _install_piper(monkeypatch, FakeVoiceCurrent)
    tts = PiperTTS(TTSConfig(voice_path="/v.onnx"))
    pcm = tts.synthesize("hi")
    assert pcm == b"\xaa\xbb\xcc\xdd"
    assert tts.sample_rate == 22050  # from the AudioChunk


def test_piper_loads_voice_once(monkeypatch):
    _install_piper(monkeypatch, FakeVoiceClassic)
    tts = PiperTTS(TTSConfig(voice_path="/v.onnx"))
    tts.synthesize("a")
    first = tts._voice
    tts.synthesize("b")
    assert tts._voice is first
