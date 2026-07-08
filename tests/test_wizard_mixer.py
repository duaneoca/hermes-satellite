"""Wizard ALSA mixer wrapper."""

import subprocess
import types

import pytest

from hermes_satellite.wizard import mixer

AMIXER_SGET = """Simple mixer control 'Capture',0
  Capabilities: cvolume cswitch
  Limits: Capture 0 - 63
  Front Left: Capture 40 [63%] [12.00dB] [on]
  Front Right: Capture 40 [63%] [12.00dB] [on]
"""


def _fake_run(record, returncode=0, stdout=AMIXER_SGET):
    def run(args, capture_output=True, text=True, timeout=10):
        record.append(args)
        return types.SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=""
        )
    return run


def test_list_cards_parses_proc(tmp_path):
    proc = tmp_path / "cards"
    proc.write_text(
        " 0 [Headphones     ]: bcm2835 - bcm2835 Headphones\n"
        " 3 [seeed2micvoicec]: seeed2micvoicec\n"
    )
    cards = mixer.list_cards(str(proc))
    assert cards == [{"index": 0, "id": "Headphones"},
                     {"index": 3, "id": "seeed2micvoicec"}]
    assert mixer.list_cards(str(tmp_path / "missing")) == []


def test_get_controls_parses_value_max_switch(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    controls = mixer.get_controls("3")
    assert controls["Capture"] == {"value": 40, "max": 63, "switch": "on"}
    assert ["amixer", "-c", "3", "sget", "Capture"] in calls


def test_set_control_whitelists_and_clamps(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    result = mixer.set_control("3", "Capture", 999)
    assert result == {"control": "Capture", "value": 63}  # clamped to max
    assert ["amixer", "-c", "3", "sset", "Capture", "63", "cap"] in calls

    with pytest.raises(ValueError):
        mixer.set_control("3", "Bogus Control; rm -rf /", 1)


def test_set_control_non_capture_has_no_cap_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    mixer.set_control("3", "ADC PCM", 220)
    assert ["amixer", "-c", "3", "sset", "ADC PCM", "220"] in calls


def test_apply_recipe_reports_applied_and_failed(monkeypatch):
    calls = []

    def run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        rc = 1 if "Speaker DC" in args else 0
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="nope")

    monkeypatch.setattr(subprocess, "run", run)
    result = mixer.apply_recipe("3")
    assert "Capture" in result["applied"]
    assert result["failed"] == ["Speaker DC"]
    # capture line keeps the switch-enable flag
    assert ["amixer", "-c", "3", "sset", "Capture", "63", "cap"] in calls


def test_store_reports_root_hint_on_failure(monkeypatch):
    def run(args, capture_output=True, text=True, timeout=10):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="Permission denied")

    monkeypatch.setattr(subprocess, "run", run)
    result = mixer.store()
    assert result["ok"] is False
    assert "sudo alsactl store" in result["hint"]
