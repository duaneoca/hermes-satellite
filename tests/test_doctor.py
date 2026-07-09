"""Shared health checks + the doctor CLI exit-code contract."""

from hermes_satellite import doctor as doctor_mod
from hermes_satellite.config import load_config
from hermes_satellite.doctor import Check, run_checks, run_doctor


def _config(tmp_path, extra=""):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "hardware_profile: mock\n"
        "wakeword:\n  model_path: hey_jarvis\n"
        f"data_dir: {tmp_path}\n"
        "audio:\n  backend: mock\n" + extra
    )
    return load_config(str(cfg_file))


def test_run_checks_shapes(tmp_path, monkeypatch):
    from hermes_satellite.wizard import mixer as mixer_mod
    monkeypatch.setattr(mixer_mod, "list_cards",
                        lambda *a, **k: [{"index": 0, "id": "seeed2micvoicec"}])
    checks = {c.name: c for c in run_checks(_config(tmp_path))}
    assert checks["profile"].detail == "mock"
    assert checks["profile"].ok is None
    assert checks["alsa_cards"].ok is True
    assert checks["data_dir"].ok is True  # tmp_path exists
    assert "hermes_health" in checks


def test_wake_models_not_checked_for_other_backends(tmp_path):
    config = _config(tmp_path)
    config.wakeword.backend = "porcupine"
    check = doctor_mod._check_wake_models(config)
    assert check.ok is None
    assert "not checked" in check.detail


def test_stt_cache_detection(tmp_path):
    config = _config(tmp_path)
    check = doctor_mod._check_stt_cache(config)
    assert check.ok is None  # informational: not downloaded yet
    assert "Transcription test" in check.detail
    cache = tmp_path / "cache" / "moonshine_voice"
    cache.mkdir(parents=True)
    (cache / "model.bin").write_bytes(b"x")
    check = doctor_mod._check_stt_cache(config)
    assert check.ok is True
    assert str(cache) == check.detail


def test_stt_cache_not_checked_for_other_backends(tmp_path):
    config = _config(tmp_path)
    config.stt.backend = "mock"
    assert doctor_mod._check_stt_cache(config).ok is None


def test_run_doctor_exit_codes(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(doctor_mod, "run_checks", lambda cfg: [
        Check("a", True, "fine"), Check("b", None, "info"),
    ])
    assert run_doctor(config) == 0
    assert "all checks passed" in capsys.readouterr().out

    monkeypatch.setattr(doctor_mod, "run_checks", lambda cfg: [
        Check("a", True, "fine"), Check("b", False, "broken thing"),
    ])
    assert run_doctor(config) == 1
    out = capsys.readouterr().out
    assert "✗ b" in out
    assert "1 check(s) failed" in out


def test_cli_doctor_subcommand(tmp_path, monkeypatch):
    from hermes_satellite.cli import main
    monkeypatch.setattr(doctor_mod, "run_checks",
                        lambda cfg: [Check("a", True, "fine")])
    cfg = tmp_path / "config.yaml"
    cfg.write_text("hardware_profile: mock\n"
                   "wakeword:\n  model_path: hey_jarvis\n"
                   f"data_dir: {tmp_path}\n")
    assert main(["doctor", "--config", str(cfg)]) == 0


def test_wake_models_matches_versioned_filenames(tmp_path, monkeypatch):
    """Regression: pretrained files carry a version suffix on disk
    (hey_jarvis_v0.1.onnx) — the check reported a working install as
    missing because it globbed for hey_jarvis.* exactly."""
    import sys
    import types as t

    pkg_dir = tmp_path / "openwakeword"
    models = pkg_dir / "resources" / "models"
    models.mkdir(parents=True)
    fake = t.ModuleType("openwakeword")
    fake.__file__ = str(pkg_dir / "__init__.py")
    monkeypatch.setitem(sys.modules, "openwakeword", fake)

    config = _config(tmp_path)
    # nothing downloaded yet
    assert doctor_mod._check_wake_models(config).ok is False
    # versioned model file + shared feature models, as shipped
    (models / "hey_jarvis_v0.1.onnx").write_bytes(b"x")
    (models / "embedding_model.onnx").write_bytes(b"x")
    check = doctor_mod._check_wake_models(config)
    assert check.ok is True, check.detail
