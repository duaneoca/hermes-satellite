import textwrap

import pytest

from hermes_satellite.config import ConfigError, load_config


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(text))
    return str(path)


BASE = """
    hardware_profile: pi4-respeaker-v1
    wakeword:
      model_path: /models/hey.ppn
    hermes:
      host: example
      port: 9000
"""


def test_load_minimal_and_profile_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, BASE))
    assert cfg.hardware_profile == "pi4-respeaker-v1"
    assert cfg.wakeword.model_path == "/models/hey.ppn"
    assert cfg.hermes.host == "example"
    assert cfg.hermes.port == 9000
    # LED SPI defaults come from the Pi 4 profile.
    assert cfg.leds.spi_bus == 0
    assert cfg.leds.spi_device == 1


def test_profile_override(tmp_path):
    cfg = load_config(_write(tmp_path, BASE), profile_override="mock")
    assert cfg.hardware_profile == "mock"


def test_env_overrides_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_API_KEY", "env-key")
    monkeypatch.setenv("HERMES_SESSION_KEY", "env-session")
    monkeypatch.setenv("PORCUPINE_ACCESS_KEY", "env-access")
    cfg = load_config(_write(tmp_path, BASE))
    assert cfg.hermes.api_key == "env-key"
    assert cfg.hermes.session_key == "env-session"
    assert cfg.wakeword.access_key == "env-access"


def test_missing_model_path_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, """
            hardware_profile: mock
            wakeword: {}
        """))


def test_builtin_keyword_accepted_without_model_path(tmp_path):
    cfg = load_config(_write(tmp_path, """
        hardware_profile: mock
        wakeword:
          backend: porcupine
          builtin_keyword: computer
    """))
    assert cfg.wakeword.builtin_keyword == "computer"
    assert cfg.wakeword.model_path == ""


def test_openwakeword_default_backend_and_tuning_fields(tmp_path):
    cfg = load_config(_write(tmp_path, """
        hardware_profile: mock
        wakeword:
          model_path: hey_jarvis
          threshold: 0.6
          patience_frames: 2
          vad_threshold: 0.4
          verifier_model_path: /v.pkl
    """))
    assert cfg.wakeword.backend == "openwakeword"
    assert cfg.wakeword.threshold == 0.6
    assert cfg.wakeword.patience_frames == 2
    assert cfg.wakeword.vad_threshold == 0.4
    assert cfg.wakeword.verifier_model_path == "/v.pkl"
    assert cfg.wakeword.inference_framework == "onnx"


def test_openwakeword_requires_model_path(tmp_path):
    with pytest.raises(ConfigError, match="openwakeword"):
        load_config(_write(tmp_path, """
            hardware_profile: mock
            wakeword:
              backend: openwakeword
        """))


def test_unknown_wakeword_backend_raises(tmp_path):
    with pytest.raises(ConfigError, match="snowboy"):
        load_config(_write(tmp_path, """
            hardware_profile: mock
            wakeword:
              backend: snowboy
              model_path: /x
        """))


def test_audio_capture_tuning_fields(tmp_path):
    cfg = load_config(_write(tmp_path, """
        hardware_profile: mock
        wakeword:
          model_path: /x.ppn
        audio:
          input_channels: 2
          silence_ms: 500
          speech_timeout_seconds: 3.5
          max_record_seconds: 8
    """))
    assert cfg.audio.input_channels == 2
    assert cfg.audio.silence_ms == 500
    assert cfg.audio.speech_timeout_seconds == 3.5
    assert cfg.audio.max_record_seconds == 8


def test_missing_profile_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, """
            wakeword:
              model_path: /x.ppn
        """))


def test_unknown_profile_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, """
            hardware_profile: pi9-imaginary
            wakeword:
              model_path: /x.ppn
        """))


def test_unknown_key_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, """
            hardware_profile: mock
            wakeword:
              model_path: /x.ppn
            hermes:
              bogus_key: 1
        """))


def test_explicit_led_spi_overrides_profile(tmp_path):
    cfg = load_config(_write(tmp_path, """
        hardware_profile: pi5-respeaker-v2
        wakeword:
          model_path: /x.ppn
        leds:
          spi_bus: 10
          spi_device: 1
    """))
    assert cfg.leds.spi_bus == 10
    assert cfg.leds.spi_device == 1


def test_missing_file_raises():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config.yaml")


def test_log_level_parses_and_validates(tmp_path):
    cfg = load_config(_write(tmp_path, """
        hardware_profile: mock
        log_level: warning
        wakeword:
          model_path: hey_jarvis
    """))
    assert cfg.log_level == "WARNING"
    with pytest.raises(ConfigError, match="log_level"):
        load_config(_write(tmp_path, """
            hardware_profile: mock
            log_level: chatty
            wakeword:
              model_path: hey_jarvis
        """))


def test_secrets_env_file_next_to_config_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    (tmp_path / "secrets.env").write_text(
        "HERMES_API_KEY=from-file\n# comment\nMQTT_PASSWORD=broker-pw\n")
    cfg = load_config(_write(tmp_path, """
        hardware_profile: mock
        wakeword:
          model_path: hey_jarvis
    """))
    assert cfg.hermes.api_key == "from-file"
    assert cfg.mqtt.password == "broker-pw"


def test_real_env_beats_secrets_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_API_KEY", "from-env")
    (tmp_path / "secrets.env").write_text("HERMES_API_KEY=from-file\n")
    cfg = load_config(_write(tmp_path, """
        hardware_profile: mock
        wakeword:
          model_path: hey_jarvis
    """))
    assert cfg.hermes.api_key == "from-env"
