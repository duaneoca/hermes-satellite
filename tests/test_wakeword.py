"""Porcupine backend logic, tested with a fake pvporcupine module."""

import sys
import types

import pytest

from hermes_satellite.config import WakeWordConfig
from hermes_satellite.wakeword.porcupine_backend import PorcupineWakeWord

FRAME_LENGTH = 4


class FakeHandle:
    sample_rate = 16000
    frame_length = FRAME_LENGTH

    def __init__(self, results):
        self._results = list(results)
        self.processed = 0

    def process(self, frame):
        assert len(frame) == FRAME_LENGTH  # unpacked int16 tuple
        self.processed += 1
        return self._results.pop(0) if self._results else -1

    def delete(self):
        pass


class FakeMic:
    def __init__(self):
        self.reads = 0

    def start(self):
        pass

    def flush(self):
        pass

    def read(self, n):
        self.reads += 1
        return b"\x00\x00" * n


@pytest.fixture
def fake_pvporcupine(monkeypatch):
    module = types.ModuleType("pvporcupine")
    module.created_with = None

    def create(**kwargs):
        module.created_with = kwargs
        return module.handle

    module.create = create
    module.handle = FakeHandle([-1, -1, 0])
    monkeypatch.setitem(sys.modules, "pvporcupine", module)
    return module


def _config(**kw):
    defaults = dict(
        backend="porcupine", model_path="/x.ppn", access_key="key", sensitivity=0.7
    )
    defaults.update(kw)
    return WakeWordConfig(**defaults)


def test_detects_wake_after_frames(fake_pvporcupine):
    mic = FakeMic()
    ww = PorcupineWakeWord(_config(), mic=mic)
    assert ww.wait_for_wake(lambda: False) is True
    assert fake_pvporcupine.handle.processed == 3  # -1, -1, then 0 = detection
    assert fake_pvporcupine.created_with["keyword_paths"] == ["/x.ppn"]
    assert fake_pvporcupine.created_with["sensitivities"] == [0.7]


def test_builtin_keyword_used_when_no_model_path(fake_pvporcupine):
    ww = PorcupineWakeWord(_config(model_path="", builtin_keyword="computer"), mic=FakeMic())
    ww.wait_for_wake(lambda: False)
    assert fake_pvporcupine.created_with["keywords"] == ["computer"]
    assert "keyword_paths" not in fake_pvporcupine.created_with


def test_muted_frames_drained_but_not_processed(fake_pvporcupine):
    mic = FakeMic()
    ww = PorcupineWakeWord(_config(), mic=mic)
    muted = {"n": 0}

    def is_muted():
        muted["n"] += 1
        return muted["n"] <= 5  # muted for the first 5 frames

    assert ww.wait_for_wake(is_muted) is True
    # 5 muted reads drained without processing, then 3 processed frames.
    assert mic.reads == 8
    assert fake_pvporcupine.handle.processed == 3


def test_stop_interrupts_wait(fake_pvporcupine):
    fake_pvporcupine.handle = FakeHandle([])  # never detects
    ww = PorcupineWakeWord(_config(), mic=FakeMic())
    ww.stop()
    assert ww.wait_for_wake(lambda: False) is False


def test_missing_access_key_raises(fake_pvporcupine):
    ww = PorcupineWakeWord(_config(access_key=""), mic=FakeMic())
    with pytest.raises(RuntimeError, match="AccessKey"):
        ww.wait_for_wake(lambda: False)


def test_sample_rate_mismatch_raises(fake_pvporcupine):
    fake_pvporcupine.handle.sample_rate = 8000
    ww = PorcupineWakeWord(_config(), mic=FakeMic())
    with pytest.raises(RuntimeError, match="Hz"):
        ww.wait_for_wake(lambda: False)


def test_requires_mic():
    with pytest.raises(ValueError):
        PorcupineWakeWord(_config(), mic=None)
