"""Runtime settings layer."""

from pathlib import Path

import pytest
import yaml

from hermes_satellite.config import load_config
from hermes_satellite.core.settings import RuntimeSettings


def _config(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "hardware_profile: mock\n"
        "wakeword:\n  model_path: hey_jarvis\n"
        f"data_dir: {tmp_path}\n"
    )
    return load_config(str(cfg_file))


def _settings(tmp_path, config=None):
    config = config or _config(tmp_path)
    return config, RuntimeSettings(config, tmp_path / "runtime.yaml")


def test_set_applies_to_live_config_and_persists(tmp_path):
    config, settings = _settings(tmp_path)
    settings.set("volume", 0.5)
    settings.set("wake_threshold", 0.7)
    assert config.tts.volume == 0.5
    assert config.wakeword.threshold == 0.7
    saved = yaml.safe_load((tmp_path / "runtime.yaml").read_text())
    assert saved["volume"] == 0.5
    assert saved["wake_threshold"] == 0.7


def test_values_clamped_and_cast(tmp_path):
    config, settings = _settings(tmp_path)
    assert settings.set("volume", "1.7") == 1.0        # str cast + clamp high
    assert settings.set("wake_threshold", 0.001) == 0.05  # clamp low
    assert settings.set("led_brightness", 99.9) == 31  # float -> int + clamp


def test_load_reapplies_persisted_overlay(tmp_path):
    config, settings = _settings(tmp_path)
    settings.set("voice", "en_GB-alan-medium")
    settings.set("volume", 0.4)

    fresh_config, fresh = _settings(tmp_path)
    fresh.load()
    assert fresh_config.tts.voice == "en_GB-alan-medium"
    assert fresh_config.tts.volume == 0.4


def test_none_default_for_unset_optional(tmp_path):
    config, settings = _settings(tmp_path)
    assert settings.get("length_scale") == 1.0  # underlying field is None
    settings.set("length_scale", 1.2)
    assert config.tts.length_scale == 1.2


def test_unknown_key_raises_and_bad_persisted_key_ignored(tmp_path):
    config, settings = _settings(tmp_path)
    with pytest.raises(KeyError):
        settings.set("bogus", 1)
    (tmp_path / "runtime.yaml").write_text("bogus: 1\nvolume: 0.3\n")
    settings.load()
    assert config.tts.volume == 0.3


def test_listener_notified_with_applied_value(tmp_path):
    _, settings = _settings(tmp_path)
    seen = []
    settings.subscribe(lambda k, v: seen.append((k, v)))
    settings.set("volume", 2.0)
    assert seen == [("volume", 1.0)]


def test_missing_path_is_harmless(tmp_path):
    config = _config(tmp_path)
    settings = RuntimeSettings(config, None)
    settings.load()
    settings.set("volume", 0.9)  # no crash without a persistence path
    assert config.tts.volume == 0.9
