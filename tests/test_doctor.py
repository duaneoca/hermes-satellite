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


def test_profiles_for_board():
    from hermes_satellite.platform import profiles_for_board
    assert profiles_for_board("Raspberry Pi Zero 2 W Rev 1.0") == ("pizero2-respeaker-v1",)
    assert profiles_for_board("Raspberry Pi 4 Model B Rev 1.5") == ("pi4-respeaker-v1",)
    assert set(profiles_for_board("Raspberry Pi 5 Model B Rev 1.0")) == {
        "pi5-respeaker-v1", "pi5-respeaker-v2"}
    assert profiles_for_board("Raspberry Pi 3 Model B") == ()


def test_board_check_pi5_accepts_both_hats(tmp_path, monkeypatch):
    from hermes_satellite.wizard import mixer as mixer_mod
    monkeypatch.setattr(mixer_mod, "list_cards", lambda *a, **k: [])
    monkeypatch.setattr(doctor_mod, "_board_model",
                        lambda path="/x": "Raspberry Pi 5 Model B Rev 1.0")
    config = _config(tmp_path)
    for profile in ("pi5-respeaker-v1", "pi5-respeaker-v2"):
        config.hardware_profile = profile
        assert {c.name: c for c in run_checks(config)}["board"].ok is True
    config.hardware_profile = "pi4-respeaker-v1"
    board = {c.name: c for c in run_checks(config)}["board"]
    assert board.ok is False
    assert "pi5-respeaker-v1" in board.detail and "pi5-respeaker-v2" in board.detail


def test_board_check_zero2(tmp_path, monkeypatch):
    from hermes_satellite.wizard import mixer as mixer_mod
    monkeypatch.setattr(mixer_mod, "list_cards", lambda *a, **k: [])
    monkeypatch.setattr(doctor_mod, "_board_model",
                        lambda path="/x": "Raspberry Pi Zero 2 W Rev 1.0")
    config = _config(tmp_path)
    config.hardware_profile = "pi4-respeaker-v1"
    board = {c.name: c for c in run_checks(config)}["board"]
    assert board.ok is False
    assert "pizero2-respeaker-v1" in board.detail
    config.hardware_profile = "pizero2-respeaker-v1"
    assert {c.name: c for c in run_checks(config)}["board"].ok is True


def _meminfo(tmp_path, ram_kb, swap_kb):
    p = tmp_path / "meminfo"
    p.write_text(f"MemTotal:       {ram_kb} kB\nSwapTotal:      {swap_kb} kB\n")
    return str(p)


def test_memory_check(tmp_path):
    config = _config(tmp_path)
    # big board: informational only
    check = doctor_mod._check_memory(config, _meminfo(tmp_path, 4_000_000, 0))
    assert check.ok is None
    # Zero 2 W without swap: fails, suggests swap + tiny model
    check = doctor_mod._check_memory(config, _meminfo(tmp_path, 427_000, 102_396))
    assert check.ok is False
    assert "swap" in check.detail and "moonshine/tiny" in check.detail
    # swap present but base model: passes, still hints
    check = doctor_mod._check_memory(config, _meminfo(tmp_path, 427_000, 1_048_572))
    assert check.ok is True and "moonshine/tiny" in check.detail
    # fully tuned
    config.stt.model = "moonshine/tiny"
    check = doctor_mod._check_memory(config, _meminfo(tmp_path, 427_000, 1_048_572))
    assert check.ok is True and "low-RAM" not in check.detail
    # no /proc/meminfo (macOS dev box)
    assert doctor_mod._check_memory(config, str(tmp_path / "nope")).ok is None
