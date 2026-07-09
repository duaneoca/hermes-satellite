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
    # every config section must survive a wizard save (regression: the
    # earcons/conversation sections were once silently dropped)
    from hermes_satellite.config import Config
    import dataclasses
    yaml_sections = {f.name for f in dataclasses.fields(Config)
                     if f.name not in ("profile",)}
    missing = yaml_sections - set(saved)
    assert not missing, f"wizard save dropped config sections: {missing}"
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


def test_preview_caches_loaded_voice(wizard, monkeypatch, tmp_path):
    """Second preview of the same voice must not reload the model."""
    import sys
    import types as t
    loads = []

    class FakeVoice:
        config = t.SimpleNamespace(sample_rate=16000)

        @classmethod
        def load(cls, path):
            loads.append(path)
            return cls()

        def synthesize_stream_raw(self, text, **kw):
            yield b"\x00\x00"

    piper_pkg = t.ModuleType("piper")
    voice_mod = t.ModuleType("piper.voice")
    voice_mod.PiperVoice = FakeVoice
    piper_pkg.voice = voice_mod
    piper_pkg.PiperVoice = FakeVoice
    monkeypatch.setitem(sys.modules, "piper", piper_pkg)
    monkeypatch.setitem(sys.modules, "piper.voice", voice_mod)

    state, base = wizard
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "en_GB-test-low.onnx").write_bytes(b"x")
    state.config.tts.voices_dir = str(voices)

    for speaker in ("", "2"):  # knob change must not force a reload
        code, r = _post(f"{base}/api/voices/preview?token={state.token}",
                        {"name": "en_GB-test-low", "speaker_id": speaker})
        assert r.get("ok"), r
        assert r["downloaded"] is False
    assert len(loads) == 1
    # the knob landed on the cached config
    assert state._tts_cache["en_GB-test-low"][0].speaker_id == 2


def test_status_warns_when_seeed_card_missing(wizard, monkeypatch):
    from hermes_satellite.wizard import mixer as mixer_mod
    monkeypatch.setattr(mixer_mod, "list_cards",
                        lambda *a, **k: [{"index": 0, "id": "Headphones"}])
    state, base = wizard
    state.config.hardware_profile = "pi4-respeaker-v1"
    _, status = _get(f"{base}/api/status?token={state.token}")
    assert status["alsa_cards"].startswith("✗")
    assert "Headphones" in status["alsa_cards"]
    assert "overlay" in status["alsa_cards"]
    # with the seeed card present, the check passes
    monkeypatch.setattr(mixer_mod, "list_cards",
                        lambda *a, **k: [{"index": 3, "id": "seeed2micvoicec"}])
    _, status = _get(f"{base}/api/status?token={state.token}")
    assert status["alsa_cards"] == "✓ seeed2micvoicec"


def test_ensure_voices_dir(tmp_path, monkeypatch):
    import subprocess
    import types
    from hermes_satellite.wizard.server import _ensure_voices_dir

    cfg = types.SimpleNamespace(
        tts=types.SimpleNamespace(voices_dir=str(tmp_path / "data" / "voices")),
        data_dir=str(tmp_path / "data"),
    )
    # creatable: plain mkdir, no sudo involved
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a) or None)
    _ensure_voices_dir(cfg)
    assert (tmp_path / "data" / "voices").is_dir()
    assert calls == []

    # uncreatable: escalate via sudo -n with the right fixed commands
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    cfg2 = types.SimpleNamespace(
        tts=types.SimpleNamespace(voices_dir=str(locked / "x" / "voices")),
        data_dir=str(locked / "x"),
    )
    sudo_calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        sudo_calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    _ensure_voices_dir(cfg2)
    assert sudo_calls[0][:3] == ["sudo", "-n", "mkdir"]
    assert sudo_calls[1][:3] == ["sudo", "-n", "chown"]

    # sudo denied: clear hint with the exact commands
    def fail_run(args, capture_output=True, text=True, timeout=10):
        return types.SimpleNamespace(returncode=1)

    monkeypatch.setattr(subprocess, "run", fail_run)
    with pytest.raises(RuntimeError, match="sudo mkdir -p"):
        _ensure_voices_dir(cfg2)
    locked.chmod(0o755)


def test_save_strips_secrets_into_secrets_env(wizard, tmp_path, monkeypatch):
    import os
    import yaml
    state, base = wizard
    state.config.hermes.api_key = "sk-supersecret-1234"
    state.config.mqtt.password = "broker-pw"
    code, result = _post(f"{base}/api/save?token={state.token}")
    # no credentials anywhere in the yaml
    text = open(state.config_path).read()
    assert "supersecret" not in text and "broker-pw" not in text
    saved = yaml.safe_load(open(state.config_path))
    assert saved["hermes"]["api_key"] == ""
    assert saved["mqtt"]["password"] == ""
    # secrets landed in a 0600 sibling env file
    assert result["secrets"].endswith("secrets.env")
    content = open(result["secrets"]).read()
    assert "HERMES_API_KEY=sk-supersecret-1234" in content
    assert "MQTT_PASSWORD=broker-pw" in content
    assert oct(os.stat(result["secrets"]).st_mode & 0o777) == "0o600"
    # and the loader picks them right back up
    from hermes_satellite.config import load_config
    reloaded = load_config(state.config_path)
    assert reloaded.hermes.api_key == "sk-supersecret-1234"


def test_status_warns_on_board_profile_mismatch(wizard, monkeypatch):
    from hermes_satellite import doctor as doctor_mod
    monkeypatch.setattr(doctor_mod, "_board_model",
                        lambda path="/x": "Raspberry Pi 4 Model B Rev 1.5")
    state, base = wizard
    state.config.hardware_profile = "pi5-respeaker-v2"
    _, status = _get(f"{base}/api/status?token={state.token}")
    assert status["board"].startswith("✗")
    assert "Raspberry Pi 4" in status["board"]
    assert "pi4-respeaker-v1" in status["board"]
    # matching profile: check passes
    state.config.hardware_profile = "pi4-respeaker-v1"
    _, status = _get(f"{base}/api/status?token={state.token}")
    assert status["board"] == "✓ Raspberry Pi 4 Model B Rev 1.5"


def test_pa_alsa_plughw_defaulted():
    import os
    import hermes_satellite  # noqa: F401  (import side effect)
    assert os.environ.get("PA_ALSA_PLUGHW") == "1"


def test_mqtt_prefill_masks_password(wizard):
    state, base = wizard
    state.config.mqtt.host = "broker.local"
    state.config.mqtt.username = "sat"
    state.config.mqtt.password = "broker-password-1234"
    code, body = _get(f"{base}/api/mqtt?token={state.token}")
    assert body["host"] == "broker.local"
    assert body["password_hint"] == "••••1234"
    assert "broker-password" not in json.dumps(body)


def test_mqtt_enable_toggle_pends(wizard):
    state, base = wizard
    _post(f"{base}/api/mqtt/config?token={state.token}", {"enabled": True})
    assert state.config.mqtt.enabled is True
    _, pending = _get(f"{base}/api/pending?token={state.token}")
    assert pending["mqtt.enabled"] is True


def test_mqtt_test_success_pends_settings(wizard, monkeypatch):
    import sys
    import types as t

    class FakeClient:
        def __init__(self, *a, **k):
            self.on_connect = None

        def username_pw_set(self, u, p):
            pass

        def connect_async(self, host, port):
            self._hp = (host, port)

        def loop_start(self):
            self.on_connect(self, None, None, 0)

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

    pkg = t.ModuleType("paho"); m = t.ModuleType("paho.mqtt")
    c = t.ModuleType("paho.mqtt.client"); c.Client = FakeClient
    pkg.mqtt = m; m.client = c
    monkeypatch.setitem(sys.modules, "paho", pkg)
    monkeypatch.setitem(sys.modules, "paho.mqtt", m)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", c)

    state, base = wizard
    code, body = _post(f"{base}/api/mqtt/test?token={state.token}",
                       {"host": "b.local", "port": 1883,
                        "username": "sat", "password": "pw"})
    assert body["ok"] is True
    assert state.config.mqtt.host == "b.local"
    assert state.config.mqtt.password == "pw"
    # and save() must strip that password into secrets.env
    _, result = _post(f"{base}/api/save?token={state.token}")
    import yaml
    assert yaml.safe_load(open(state.config_path))["mqtt"]["password"] == ""
    assert "MQTT_PASSWORD=pw" in open(result["secrets"]).read()


def test_save_quotes_secrets_with_spaces(wizard):
    state, base = wizard
    state.config.mqtt.password = "has spaces #and hash"
    _, result = _post(f"{base}/api/save?token={state.token}")
    content = open(result["secrets"]).read()
    assert 'MQTT_PASSWORD="has spaces #and hash"' in content
    # round-trip through the loader
    from hermes_satellite.config import load_config
    assert load_config(state.config_path).mqtt.password == "has spaces #and hash"


def test_behavior_prefill_reflects_config(wizard):
    state, base = wizard
    _, body = _get(f"{base}/api/behavior?token={state.token}")
    assert body["stream"] is True          # hermes.stream default
    assert body["follow_up"] is False      # conversation default
    assert body["follow_up_seconds"] == 6.0
    assert body["max_turns"] == 8
    assert body["earcons"] is True
    assert body["earcon_volume"] == 0.5
    assert body["barge_in"] is False


def test_behavior_config_pends_and_applies_live(wizard):
    state, base = wizard
    # numbers arrive as strings from the page's inputs
    _post(f"{base}/api/behavior/config?token={state.token}",
          {"follow_up": True, "follow_up_seconds": "8", "barge_in": True,
           "max_turns": "4", "earcon_volume": "0.3", "stream": False})
    assert state.config.conversation.follow_up is True
    assert state.config.conversation.barge_in is True
    assert state.config.conversation.follow_up_seconds == 8.0
    assert state.config.conversation.max_turns == 4
    assert state.config.earcons.volume == 0.3
    assert state.config.hermes.stream is False
    _, pending = _get(f"{base}/api/pending?token={state.token}")
    assert pending["conversation.follow_up"] is True
    assert pending["hermes.stream"] is False
    # partial updates leave the rest alone
    _post(f"{base}/api/behavior/config?token={state.token}",
          {"earcons": False})
    assert state.config.earcons.enabled is False
    assert state.config.conversation.max_turns == 4


def test_page_has_conversation_section(wizard):
    """Regression: the earcons/follow-up/streaming settings shipped without
    any wizard UI — the page must render the section and its controls."""
    state, base = wizard
    with urllib.request.urlopen(f"{base}/?token={state.token}") as r:
        html = r.read().decode()
    assert "Conversation" in html
    for element_id in ("bs", "bf", "bw", "bt", "be", "bv"):
        assert f'id="{element_id}"' in html
    assert "/api/behavior" in html


def test_stt_prepare_and_test_flow(wizard, monkeypatch):
    """Prepare loads/caches the engine (into the service cache location),
    test captures one utterance and transcribes it."""
    import hermes_satellite.stt as stt_pkg

    class FakeEngine:
        def __init__(self):
            self.calls = []

        def transcribe(self, audio):
            self.calls.append(audio)
            return "hello there" if len(audio) > 4000 else ""

    engine = FakeEngine()
    monkeypatch.setattr(stt_pkg, "build_stt",
                        lambda config, demo=False: engine)
    state, base = wizard  # fixture config uses audio.backend: mock

    code, p = _post(f"{base}/api/stt/prepare?token={state.token}")
    assert p["ok"] is True
    assert engine.calls  # model force-loaded with silence
    assert state._stt_engine is engine

    code, r = _post(f"{base}/api/stt/test?token={state.token}")
    assert r["transcript"] == "hello there"
    assert r["capture_seconds"] > 0
    # engine is cached: prepare again must not rebuild
    monkeypatch.setattr(stt_pkg, "build_stt",
                        lambda config, demo=False: (_ for _ in ()).throw(
                            AssertionError("rebuilt")))
    _, p2 = _post(f"{base}/api/stt/prepare?token={state.token}")
    assert p2["ok"] is True


def test_stt_test_without_prepare_is_an_error(wizard):
    state, base = wizard
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(f"{base}/api/stt/test?token={state.token}")
    assert e.value.code == 400


def test_ensure_stt_cache_targets_service_location(tmp_path, monkeypatch):
    import os
    import types
    from hermes_satellite.wizard.server import _ensure_stt_cache

    cfg = types.SimpleNamespace(data_dir=str(tmp_path / "data"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    _ensure_stt_cache(cfg)
    assert os.environ["XDG_CACHE_HOME"] == str(tmp_path / "data" / "cache")
    assert (tmp_path / "data" / "cache").is_dir()

    # an already-set XDG_CACHE_HOME is respected
    monkeypatch.setenv("XDG_CACHE_HOME", "/somewhere/else")
    _ensure_stt_cache(cfg)
    assert os.environ["XDG_CACHE_HOME"] == "/somewhere/else"


def test_page_has_transcription_section(wizard):
    state, base = wizard
    with urllib.request.urlopen(f"{base}/?token={state.token}") as r:
        html = r.read().decode()
    assert "Transcription" in html
    assert "/api/stt/prepare" in html
    assert "/api/stt/test" in html


def _fake_oww(monkeypatch, tmp_path, downloaded=()):
    """Install a fake openwakeword package with the given models on disk
    (real on-disk naming: versioned files)."""
    import sys
    import types as t

    pkg_dir = tmp_path / "openwakeword"
    models = pkg_dir / "resources" / "models"
    models.mkdir(parents=True, exist_ok=True)
    for name in downloaded:
        (models / f"{name}_v0.1.onnx").write_bytes(b"x")
    fake = t.ModuleType("openwakeword")
    fake.__file__ = str(pkg_dir / "__init__.py")
    utils = t.ModuleType("openwakeword.utils")
    fetched = []

    def download_models(model_names=None):
        for name in model_names or []:
            fetched.append(name)
            (models / f"{name}_v0.1.onnx").write_bytes(b"x")

    utils.download_models = download_models
    fake.utils = utils
    monkeypatch.setitem(sys.modules, "openwakeword", fake)
    monkeypatch.setitem(sys.modules, "openwakeword.utils", utils)
    return fetched


def test_wake_model_listing(wizard, monkeypatch, tmp_path):
    _fake_oww(monkeypatch, tmp_path, downloaded=("hey_jarvis",))
    state, base = wizard
    _, body = _get(f"{base}/api/wake/model?token={state.token}")
    assert body["current"] == "hey_jarvis"
    assert "hey_mycroft" in body["pretrained"]
    assert body["downloaded"]["hey_jarvis"] is True
    assert body["downloaded"]["hey_mycroft"] is False


def test_wake_model_switch_downloads_and_pends(wizard, monkeypatch, tmp_path):
    fetched = _fake_oww(monkeypatch, tmp_path, downloaded=("hey_jarvis",))
    state, base = wizard
    _, r = _post(f"{base}/api/wake/model?token={state.token}",
                 {"model_path": "hey_mycroft"})
    assert r["ok"] is True
    assert "downloaded" in r["note"]
    assert fetched == ["hey_mycroft"]
    assert state.config.wakeword.model_path == "hey_mycroft"
    _, pending = _get(f"{base}/api/pending?token={state.token}")
    assert pending["wakeword.model_path"] == "hey_mycroft"
    # already on disk: no second download
    _, r = _post(f"{base}/api/wake/model?token={state.token}",
                 {"model_path": "hey_jarvis"})
    assert fetched == ["hey_mycroft"]
    assert r["ok"] is True


def test_wake_model_custom_path(wizard, tmp_path):
    state, base = wizard
    custom = tmp_path / "hey_hermes.onnx"
    _, r = _post(f"{base}/api/wake/model?token={state.token}",
                 {"model_path": str(custom)})
    assert "no model file" in r["error"]
    custom.write_bytes(b"x")
    _, r = _post(f"{base}/api/wake/model?token={state.token}",
                 {"model_path": str(custom)})
    assert r["ok"] is True
    assert state.config.wakeword.model_path == str(custom)


def test_page_has_wake_model_picker(wizard):
    state, base = wizard
    with urllib.request.urlopen(f"{base}/?token={state.token}") as r:
        html = r.read().decode()
    assert 'id="wwm"' in html and 'id="wwp"' in html
    assert "/api/wake/model" in html
