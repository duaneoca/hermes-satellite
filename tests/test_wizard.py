"""Setup wizard server: auth, doctor, pending changes, save, exit."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from hermes_satellite.config import load_config
from hermes_satellite.wizard.server import WizardState, _make_handler


@pytest.fixture
def wizard(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "hardware_profile: mock\n"
        "wakeword:\n  model_path: hey_jarvis\n"
        f"data_dir: {tmp_path}\n"
        "audio:\n  backend: mock\n"
    )
    config = load_config(str(cfg_file))
    state = WizardState(config, str(cfg_file), idle_timeout_s=999)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    yield state, base
    server.shutdown()


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, json.loads(r.read())


def _post(url, payload=None):
    req = urllib.request.Request(
        url, data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())


def test_requests_without_token_rejected(wizard):
    state, base = wizard
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(f"{base}/api/status")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(f"{base}/api/status?token=wrong")
    assert e.value.code == 403


def test_page_and_status_with_token(wizard):
    state, base = wizard
    with urllib.request.urlopen(f"{base}/?token={state.token}") as r:
        html = r.read().decode()
    assert "hermes-satellite setup" in html
    assert state.token in html  # page JS carries the token
    code, status = _get(f"{base}/api/status?token={state.token}")
    assert code == 200
    assert status["profile"] == "mock"
    assert "hermes_health" in status


def test_audio_select_tracks_pending_and_applies_live(wizard):
    state, base = wizard
    _post(f"{base}/api/audio/select?token={state.token}",
          {"input_device": 3, "input_channels": 2})
    assert state.config.audio.input_device == 3
    assert state.config.audio.input_channels == 2
    code, pending = _get(f"{base}/api/pending?token={state.token}")
    assert pending["audio.input_device"] == 3


def test_wake_config_clamps_via_live_config(wizard):
    state, base = wizard
    _post(f"{base}/api/wake/config?token={state.token}", {"threshold": 0.8})
    assert state.config.wakeword.threshold == 0.8


def test_save_backs_up_then_overwrites_in_place(wizard, tmp_path):
    state, base = wizard
    original = open(state.config_path).read()
    _post(f"{base}/api/audio/select?token={state.token}", {"input_device": 1})
    code, result = _post(f"{base}/api/save?token={state.token}")
    import yaml
    # config updated in place
    assert result["written"] == state.config_path
    saved = yaml.safe_load(open(state.config_path))
    assert saved["audio"]["input_device"] == 1
    assert saved["hardware_profile"] == "mock"
    assert saved["wakeword"]["model_path"] == "hey_jarvis"
    # previous content preserved in a timestamped backup
    assert ".bak-" in result["backup"]
    assert open(result["backup"]).read() == original
    assert result["changes"] == {"audio.input_device": 1}


def test_exit_sets_shutdown_event(wizard):
    state, base = wizard
    assert not state.shutdown_event.is_set()
    _post(f"{base}/api/exit?token={state.token}")
    assert state.shutdown_event.is_set()


def test_unknown_route_404(wizard):
    state, base = wizard
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(f"{base}/api/nope?token={state.token}")
    assert e.value.code == 404


def test_mixer_route_parses_card_alongside_token(wizard, monkeypatch):
    """Regression: /api/mixer?card=3&token=... must authorize AND see the card
    (the page once produced ?card=3?token=..., which 403'd)."""
    from hermes_satellite.wizard import mixer as mixer_mod
    seen = {}

    def fake_get_controls(card):
        seen["card"] = card
        return {"Capture": {"value": 40, "max": 63, "switch": "on"}}

    monkeypatch.setattr(mixer_mod, "get_controls", fake_get_controls)
    state, base = wizard
    code, body = _get(f"{base}/api/mixer?card=3&token={state.token}")
    assert code == 200
    assert seen["card"] == "3"
    assert body["controls"]["Capture"]["value"] == 40


def test_hermes_prefill_masks_api_key(wizard):
    state, base = wizard
    state.config.hermes.host = "192.168.1.10"
    state.config.hermes.session_key = "test-sat"
    state.config.hermes.api_key = "sk-abcdef1234567890wxyz"
    code, body = _get(f"{base}/api/hermes?token={state.token}")
    assert code == 200
    assert body["host"] == "192.168.1.10"
    assert body["session_key"] == "test-sat"
    assert body["api_key_hint"] == "••••wxyz"
    # the real key must never appear anywhere in the response
    assert "abcdef" not in json.dumps(body)


def test_hermes_prefill_short_or_missing_key(wizard):
    state, base = wizard
    state.config.hermes.api_key = "short"
    _, body = _get(f"{base}/api/hermes?token={state.token}")
    assert body["api_key_hint"] == "••••"
    state.config.hermes.api_key = ""
    _, body = _get(f"{base}/api/hermes?token={state.token}")
    assert body["api_key_hint"] == ""


def test_wake_monitor_ready_flag_and_callbacks(wizard, monkeypatch):
    """ready flips only when audio actually flows; listening/stop callbacks fire."""
    import hermes_satellite.wakeword as ww_pkg
    from hermes_satellite.wizard.server import _WakeMonitor

    class FakeDetector:
        def __init__(self):
            self.on_score = None
            self.on_audio = None

        def wait_for_wake(self, is_muted):
            self.on_audio(b"\x00\x00")
            self.on_audio(b"\x00\x00")
            self.on_score({"hey_jarvis": 0.42})
            return False  # end the monitor

        def stop(self):
            pass

    monkeypatch.setattr(ww_pkg, "build_wakeword",
                        lambda config, demo=False, mic=None: FakeDetector())
    import hermes_satellite.audio.mic as mic_mod

    class FakeMic:
        def __init__(self, **kw): pass
        def start(self): pass
        def close(self): pass

    monkeypatch.setattr(mic_mod, "MicStream", FakeMic)

    state, base = wizard
    events = []
    monitor = _WakeMonitor(state.config)
    monitor.on_listening = lambda: events.append(("listening", monitor.ready))
    monitor.on_stopped = lambda: events.append(("stopped", monitor.ready))
    monitor.start()
    monitor._thread.join(timeout=5)
    assert events == [("listening", True), ("stopped", False)]
    assert monitor.last == 0.42
