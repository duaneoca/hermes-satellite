"""One-shot health checks, shared by the setup wizard and the doctor CLI.

Each check yields a :class:`Check` with ``ok`` being True (pass), False
(fail — the daemon likely won't work), or None (informational). The wizard's
Status section renders them as a table; ``hermes-satellite doctor`` prints
them with a nonzero exit code if anything failed, so it can gate scripts:

    hermes-satellite doctor --config /etc/hermes-satellite/config.yaml
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class Check:
    name: str
    ok: Optional[bool]  # True = pass, False = fail, None = informational
    detail: str


def _board_model(path: str = "/proc/device-tree/model") -> str:
    try:
        return Path(path).read_text().rstrip("\x00").strip()
    except OSError:
        return ""


def run_checks(config) -> List[Check]:
    from .wizard import mixer

    cfg = config
    checks: List[Check] = []
    checks.append(Check("profile", None, cfg.hardware_profile))

    board = _board_model()
    if board:
        expected = None
        if "Raspberry Pi 4" in board:
            expected = "pi4-respeaker-v1"
        elif "Raspberry Pi 5" in board:
            expected = "pi5-respeaker-v2"
        if expected and not cfg.hardware_profile.startswith(expected[:3]):
            checks.append(Check(
                "board", False,
                f"{board!r} but hardware_profile is "
                f"{cfg.hardware_profile!r} — set hardware_profile: {expected}",
            ))
        else:
            checks.append(Check("board", True, board))

    spidev = Path(f"/dev/spidev{cfg.leds.spi_bus}.{cfg.leds.spi_device}")
    checks.append(Check(
        "spidev", spidev.exists(),
        str(spidev) if spidev.exists() else
        f"missing {spidev} — SPI not enabled? (raspi-config / dtparam=spi=on)",
    ))

    try:
        import onnxruntime
        ver = onnxruntime.__version__
        checks.append(Check(
            "onnxruntime", ver < "1.27",
            ver if ver < "1.27" else
            f"{ver} — BROKEN for openWakeWord (all scores ~0); pin <1.27",
        ))
    except ImportError:
        checks.append(Check("onnxruntime", False, "not installed"))

    cards = [c["id"] for c in mixer.list_cards()]
    if not cards:
        checks.append(Check("alsa_cards", False, "none found"))
    elif cfg.hardware_profile.startswith("pi") and not any(
        "seeed" in c.lower() or "wm8960" in c.lower() for c in cards
    ):
        checks.append(Check(
            "alsa_cards", False,
            f"{', '.join(cards)} — no ReSpeaker/seeed card; audio overlay "
            "not installed or not rebooted? See your hardware guide, section 1",
        ))
    else:
        checks.append(Check("alsa_cards", True, ", ".join(cards)))

    checks.append(_check_wake_models(cfg))
    checks.append(_check_stt_cache(cfg))

    # Informational: the daemon can download a voice on demand (its data
    # dir is writable even under the sandboxed service).
    voices = sorted(p.stem for p in Path(cfg.tts.voices_dir).glob("*.onnx")) \
        if Path(cfg.tts.voices_dir).exists() else []
    checks.append(Check(
        "voices_downloaded", None,
        ", ".join(voices) if voices else
        f"none in {cfg.tts.voices_dir} — preview one in the wizard's Voice "
        "section to download it",
    ))

    data_dir = Path(cfg.data_dir)
    checks.append(Check(
        "data_dir", data_dir.is_dir(),
        str(data_dir) if data_dir.is_dir() else f"missing {data_dir}",
    ))

    try:
        import requests
        r = requests.get(
            f"http://{cfg.hermes.host}:{cfg.hermes.port}/health", timeout=3
        )
        checks.append(Check(
            "hermes_health", r.status_code == 200, f"HTTP {r.status_code}"
        ))
    except Exception as exc:
        checks.append(Check(
            "hermes_health", False,
            f"unreachable at {cfg.hermes.host}:{cfg.hermes.port} "
            f"({type(exc).__name__})",
        ))
    return checks


def _check_wake_models(cfg) -> Check:
    """openWakeWord model files present? (The sandboxed service cannot
    download them itself — /opt is read-only to it.)"""
    if cfg.wakeword.backend.lower() != "openwakeword":
        return Check("wake_models", None,
                     f"backend {cfg.wakeword.backend} (not checked)")
    name = cfg.wakeword.model_path
    if os.path.exists(name):
        return Check("wake_models", True, name)
    try:
        import openwakeword
        resources = Path(openwakeword.__file__).parent / "resources" / "models"
    except ImportError:
        return Check("wake_models", False, "openwakeword not installed")
    # The named model plus the shared feature models it depends on.
    have_model = any(resources.glob(f"{name}.*"))
    have_features = any(resources.glob("embedding_model.*"))
    if have_model and have_features:
        return Check("wake_models", True, f"{name} (in {resources})")
    return Check(
        "wake_models", False,
        f"{name} not downloaded — pre-seed as root: sudo <venv>/bin/python "
        f"-c \"import openwakeword.utils; "
        f"openwakeword.utils.download_models(['{name}'])\"",
    )


def _check_stt_cache(cfg) -> Check:
    """Moonshine model cache — informational: it auto-downloads on first
    use interactively, but the sandboxed service needs it pre-seeded under
    {data_dir}/cache (its XDG_CACHE_HOME)."""
    if cfg.stt.backend.lower() != "moonshine":
        return Check("stt_cache", None,
                     f"backend {cfg.stt.backend} (not checked)")
    candidates = [
        Path(cfg.data_dir) / "cache" / "moonshine_voice",
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "moonshine_voice",
    ]
    for path in candidates:
        if path.is_dir() and any(path.iterdir()):
            return Check("stt_cache", True, str(path))
    return Check(
        "stt_cache", None,
        "no Moonshine model cached yet — the wizard's Transcription test "
        "downloads it, or see 'Running as a service' step 3",
    )


def run_doctor(config) -> int:
    """Print all checks; exit 1 if any hard check failed."""
    marks = {True: "✓", False: "✗", None: "·"}
    failed = 0
    for check in run_checks(config):
        if check.ok is False:
            failed += 1
        print(f" {marks[check.ok]} {check.name:18s} {check.detail}")
    if failed:
        print(f"\n{failed} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0
