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


def test_save_writes_review_file_with_changes(wizard, tmp_path):
    state, base = wizard
    _post(f"{base}/api/audio/select?token={state.token}", {"input_device": 1})
    code, result = _post(f"{base}/api/save?token={state.token}")
    assert result["written"].endswith("config.yaml.new")
    assert result["changes"] == {"audio.input_device": 1}
    assert "mv " in result["command"]
    import yaml
    saved = yaml.safe_load(open(result["written"]))
    assert saved["audio"]["input_device"] == 1
    assert saved["hardware_profile"] == "mock"
    assert saved["wakeword"]["model_path"] == "hey_jarvis"


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
