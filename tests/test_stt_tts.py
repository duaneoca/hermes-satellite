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


class FakeSynthesisConfig:
    def __init__(self, speaker_id=None, length_scale=None, volume=1.0, **kw):
        self.speaker_id = speaker_id
        self.length_scale = length_scale
        self.volume = volume


class FakeVoiceKnobs:
    """Current-API voice that records syn_config."""
    received = None

    @classmethod
    def load(cls, path):
        inst = cls()
        inst.path = path
        return inst

    def synthesize(self, text, syn_config=None):
        FakeVoiceKnobs.received = syn_config
        yield FakeChunk(b"\x01\x02")


def _install_piper_with_knobs(monkeypatch, voice_cls, download_calls=None):
    piper_pkg = types.ModuleType("piper")
    voice_mod = types.ModuleType("piper.voice")
    dl_mod = types.ModuleType("piper.download_voices")
    voice_mod.PiperVoice = voice_cls
    piper_pkg.voice = voice_mod
    piper_pkg.PiperVoice = voice_cls
    piper_pkg.SynthesisConfig = FakeSynthesisConfig
    sink = download_calls if download_calls is not None else []
    dl_mod.download_voice = lambda name, d: sink.append((name, str(d)))
    dl_mod.VOICES_JSON = "http://example/voices.json"
    piper_pkg.download_voices = dl_mod
    monkeypatch.setitem(sys.modules, "piper", piper_pkg)
    monkeypatch.setitem(sys.modules, "piper.voice", voice_mod)
    monkeypatch.setitem(sys.modules, "piper.download_voices", dl_mod)


def test_piper_voice_name_resolves_and_downloads_once(monkeypatch, tmp_path):
    calls = []
    _install_piper_with_knobs(monkeypatch, FakeVoiceKnobs, calls)
    cfg = TTSConfig(voice="en_GB-test-medium", voices_dir=str(tmp_path))
    tts = PiperTTS(cfg)
    tts.synthesize("hello")
    assert calls == [("en_GB-test-medium", str(tmp_path))]
    # once the file exists, no re-download
    (tmp_path / "en_GB-test-medium.onnx").write_bytes(b"x")
    tts2 = PiperTTS(cfg)
    tts2.synthesize("again")
    assert len(calls) == 1


def test_piper_voice_path_overrides_name(monkeypatch, tmp_path):
    calls = []
    _install_piper_with_knobs(monkeypatch, FakeVoiceKnobs, calls)
    cfg = TTSConfig(voice="ignored", voice_path="/v.onnx", voices_dir=str(tmp_path))
    PiperTTS(cfg).synthesize("hi")
    assert calls == []


def test_piper_no_voice_configured_raises(monkeypatch):
    _install_piper_with_knobs(monkeypatch, FakeVoiceKnobs)
    with pytest.raises(RuntimeError, match="tts.voice"):
        PiperTTS(TTSConfig()).synthesize("hi")


def test_piper_knobs_passed_via_synthesis_config(monkeypatch):
    _install_piper_with_knobs(monkeypatch, FakeVoiceKnobs)
    cfg = TTSConfig(voice_path="/v.onnx", speaker_id=47, length_scale=1.1, volume=0.8)
    PiperTTS(cfg).synthesize("hi")
    sc = FakeVoiceKnobs.received
    assert sc is not None
    assert (sc.speaker_id, sc.length_scale, sc.volume) == (47, 1.1, 0.8)


def test_piper_default_knobs_send_no_synthesis_config(monkeypatch):
    _install_piper_with_knobs(monkeypatch, FakeVoiceKnobs)
    FakeVoiceKnobs.received = "sentinel"
    PiperTTS(TTSConfig(voice_path="/v.onnx")).synthesize("hi")
    assert FakeVoiceKnobs.received is None


def test_piper_classic_api_gets_knob_kwargs(monkeypatch):
    received = {}

    class ClassicKnobs(FakeVoiceClassic):
        def synthesize_stream_raw(self, text, **kwargs):
            received.update(kwargs)
            yield b"\x01\x02"

    _install_piper_with_knobs(monkeypatch, ClassicKnobs)
    cfg = TTSConfig(voice_path="/v.onnx", speaker_id=3, length_scale=0.9)
    PiperTTS(cfg).synthesize("hi")
    assert received == {"speaker_id": 3, "length_scale": 0.9}
