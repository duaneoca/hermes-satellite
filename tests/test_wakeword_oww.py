"""openWakeWord backend logic, tested with a fake openwakeword module."""

import sys
import types

import pytest

from hermes_satellite.config import WakeWordConfig
from hermes_satellite.wakeword.openwakeword_backend import (
    FRAME_SAMPLES,
    OpenWakeWord,
)


class FakeModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.scores = kwargs.pop("_scores", [])
        self.models = {"hey_jarvis": object()}
        self.predict_calls = []
        self.resets = 0

    def predict(self, x, **kw):
        assert len(x) == FRAME_SAMPLES
        self.predict_calls.append(kw)
        score = self.scores.pop(0) if self.scores else 0.0
        return {"hey_jarvis": score}

    def reset(self):
        self.resets += 1


class FakeMic:
    def __init__(self):
        self.reads = 0
        self.flushes = 0

    def start(self):
        pass

    def flush(self):
        self.flushes += 1

    def read(self, n):
        self.reads += 1
        return b"\x00\x00" * n


@pytest.fixture
def fake_oww(monkeypatch):
    pkg = types.ModuleType("openwakeword")
    model_mod = types.ModuleType("openwakeword.model")
    utils_mod = types.ModuleType("openwakeword.utils")
    state = {"scores": [], "instance": None, "downloads": []}

    class Model(FakeModel):
        def __init__(self, **kwargs):
            super().__init__(_scores=list(state["scores"]), **kwargs)
            state["instance"] = self

    model_mod.Model = Model
    utils_mod.download_models = lambda model_names=None: state["downloads"].append(
        model_names
    )
    pkg.model = model_mod
    pkg.utils = utils_mod
    monkeypatch.setitem(sys.modules, "openwakeword", pkg)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_mod)
    monkeypatch.setitem(sys.modules, "openwakeword.utils", utils_mod)
    return state


def _config(**kw):
    defaults = dict(backend="openwakeword", model_path="hey_jarvis")
    defaults.update(kw)
    return WakeWordConfig(**defaults)


def test_fires_when_score_crosses_threshold(fake_oww):
    fake_oww["scores"] = [0.1, 0.2, 0.9]
    mic = FakeMic()
    ww = OpenWakeWord(_config(), mic=mic)
    assert ww.wait_for_wake(lambda: False) is True
    model = fake_oww["instance"]
    assert len(model.predict_calls) == 3
    assert mic.flushes == 1
    assert model.resets >= 2  # once on entry, once after detection


def test_threshold_respected(fake_oww):
    fake_oww["scores"] = [0.6, 0.6]
    ww = OpenWakeWord(_config(threshold=0.7), mic=FakeMic())
    # 0.6 never crosses 0.7; stop after the scripted frames run out.
    calls = {"n": 0}

    def muted():
        calls["n"] += 1
        if calls["n"] > 4:
            ww.stop()
        return False

    assert ww.wait_for_wake(muted) is False


def test_patience_passed_to_predict(fake_oww):
    fake_oww["scores"] = [0.9]
    ww = OpenWakeWord(_config(patience_frames=3, threshold=0.6), mic=FakeMic())
    ww.wait_for_wake(lambda: False)
    kw = fake_oww["instance"].predict_calls[0]
    assert kw["patience"] == {"hey_jarvis": 3}
    assert kw["threshold"] == {"hey_jarvis": 0.6}


def test_no_patience_kwargs_by_default(fake_oww):
    fake_oww["scores"] = [0.9]
    ww = OpenWakeWord(_config(), mic=FakeMic())
    ww.wait_for_wake(lambda: False)
    assert fake_oww["instance"].predict_calls[0] == {}


def test_refractory_suppresses_immediate_retrigger(fake_oww, monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(
        "hermes_satellite.wakeword.openwakeword_backend.time",
        types.SimpleNamespace(monotonic=lambda: clock["t"]),
    )
    fake_oww["scores"] = [0.9, 0.9, 0.9]
    ww = OpenWakeWord(_config(refractory_seconds=5.0), mic=FakeMic())
    assert ww.wait_for_wake(lambda: False) is True  # fires at t=100

    # Within the refractory window, high scores must not fire again.
    calls = {"n": 0}

    def muted():
        calls["n"] += 1
        if calls["n"] > 3:
            ww.stop()
        return False

    assert ww.wait_for_wake(muted) is False

    # After the window, detection works again.
    clock["t"] = 106.0
    fake_oww["instance"].scores = [0.9]
    ww._stop_event.clear()
    assert ww.wait_for_wake(lambda: False) is True


def test_muted_drains_without_predict(fake_oww):
    fake_oww["scores"] = [0.9]
    mic = FakeMic()
    ww = OpenWakeWord(_config(), mic=mic)
    muted = {"n": 0}

    def is_muted():
        muted["n"] += 1
        return muted["n"] <= 5

    assert ww.wait_for_wake(is_muted) is True
    model = fake_oww["instance"]
    assert mic.reads == 6  # 5 drained + 1 processed
    assert len(model.predict_calls) == 1
    assert model.resets >= 2  # entry reset + post-mute reset


def test_verifier_config_passed(fake_oww):
    fake_oww["scores"] = [0.9]
    ww = OpenWakeWord(
        _config(
            model_path="/models/hey_hermes.onnx",
            verifier_model_path="/models/verifier.pkl",
            verifier_threshold=0.3,
        ),
        mic=FakeMic(),
    )
    ww.wait_for_wake(lambda: False)
    kwargs = fake_oww["instance"].kwargs
    assert kwargs["custom_verifier_models"] == {"hey_hermes": "/models/verifier.pkl"}
    assert kwargs["custom_verifier_threshold"] == 0.3


def test_constructor_options_passed(fake_oww):
    fake_oww["scores"] = [0.9]
    ww = OpenWakeWord(
        _config(vad_threshold=0.5, noise_suppression=True, inference_framework="onnx"),
        mic=FakeMic(),
    )
    ww.wait_for_wake(lambda: False)
    kwargs = fake_oww["instance"].kwargs
    assert kwargs["vad_threshold"] == 0.5
    assert kwargs["enable_speex_noise_suppression"] is True
    assert kwargs["inference_framework"] == "onnx"
    assert kwargs["wakeword_models"] == ["hey_jarvis"]


def test_download_retry_on_first_failure(fake_oww, monkeypatch):
    fake_oww["scores"] = [0.9]
    attempts = {"n": 0}
    orig = sys.modules["openwakeword.model"].Model

    class FlakyModel(orig):
        def __init__(self, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise FileNotFoundError("feature models missing")
            super().__init__(**kwargs)

    sys.modules["openwakeword.model"].Model = FlakyModel
    ww = OpenWakeWord(_config(), mic=FakeMic())
    assert ww.wait_for_wake(lambda: False) is True
    assert fake_oww["downloads"] == [["hey_jarvis"]]
    assert attempts["n"] == 2


def test_requires_16k():
    with pytest.raises(RuntimeError, match="16000"):
        OpenWakeWord(_config(), mic=FakeMic(), sample_rate=44100)


def test_requires_mic():
    with pytest.raises(ValueError):
        OpenWakeWord(_config(), mic=None)


def test_on_audio_hook_receives_frames_not_while_muted(fake_oww):
    fake_oww["scores"] = [0.1, 0.9]
    ww = OpenWakeWord(_config(), mic=FakeMic())
    frames = []
    ww.on_audio = frames.append
    muted = {"n": 0}

    def is_muted():
        muted["n"] += 1
        return muted["n"] <= 2  # first two frames muted

    assert ww.wait_for_wake(is_muted) is True
    # 2 muted frames skipped, 2 processed frames delivered to the hook
    assert len(frames) == 2
    assert all(len(f) == FRAME_SAMPLES * 2 for f in frames)
