"""Earcon tone generation and playback gating."""

from hermes_satellite.config import EarconsConfig
from hermes_satellite.core.earcons import Earcons, _CUES, _render


class RecordingSink:
    def __init__(self):
        self.plays = []

    def play(self, pcm, sample_rate=None):
        self.plays.append((len(pcm), sample_rate))


def test_render_produces_expected_length_int16():
    pcm = _render("wake", sample_rate=16000, volume=0.5)
    total_ms = sum(ms for _, ms in _CUES["wake"])
    assert len(pcm) == 2 * (16000 * total_ms // 1000)  # 16-bit samples
    assert len(pcm) % 2 == 0


def test_volume_zero_is_silent_but_present():
    pcm = _render("error", sample_rate=16000, volume=0.0)
    assert set(pcm) == {0}  # all-zero samples, correct length


def test_disabled_earcons_do_not_play():
    sink = RecordingSink()
    Earcons(EarconsConfig(enabled=False), sink).play("wake")
    assert sink.plays == []


def test_enabled_earcons_play_and_cache():
    sink = RecordingSink()
    ec = Earcons(EarconsConfig(enabled=True, volume=0.5), sink, sample_rate=22050)
    ec.play("wake")
    ec.play("wake")
    assert len(sink.plays) == 2
    assert sink.plays[0][1] == 22050
    assert ec._cache["wake"] is not None  # rendered once, reused


def test_unknown_cue_is_ignored():
    sink = RecordingSink()
    Earcons(EarconsConfig(enabled=True), sink).play("nonexistent")
    assert sink.plays == []


def test_playback_failure_is_swallowed():
    class BadSink:
        def play(self, pcm, sample_rate=None):
            raise RuntimeError("device busy")

    Earcons(EarconsConfig(enabled=True), BadSink()).play("wake")  # no raise
