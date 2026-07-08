"""ALSA mixer control for the wizard (wraps ``amixer``, no new deps).

Only whitelisted controls can be touched, values are clamped to each
control's range, and subprocesses run without a shell — a web request can
never smuggle arbitrary amixer/shell arguments.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Whitelist: control name -> conservative max (real max re-read from amixer).
CONTROLS = {
    "Capture": 63,
    "ADC PCM": 255,
    "Playback": 255,
    "Speaker": 127,
    "Headphone": 127,
    "Speaker DC": 5,
    "Speaker AC": 5,
}

# The field-verified WM8960 routing + calibration recipe
# (docs/hardware/pi4-respeaker-v1.md); mirrors scripts/wm8960-mixer.sh.
RECIPE = (
    ("Left Input Mixer Boost", ["on"]),
    ("Right Input Mixer Boost", ["on"]),
    ("Left Boost Mixer LINPUT1", ["on"]),
    ("Right Boost Mixer RINPUT1", ["on"]),
    ("Capture", ["63", "cap"]),
    ("ADC PCM", ["220"]),
    ("ALC Function", ["Off"]),
    ("Left Output Mixer PCM", ["on"]),
    ("Right Output Mixer PCM", ["on"]),
    ("Playback", ["255"]),
    ("Speaker", ["121"]),
    ("Headphone", ["110"]),
    ("Speaker DC", ["5"]),
    ("Speaker AC", ["5"]),
)

_CARD_LINE = re.compile(r"^\s*(\d+)\s+\[(\S+?)\s*\]", re.MULTILINE)
_VALUE = re.compile(r":\s+(?:Capture|Playback)\s+(\d+)\s+\[")
_LIMITS = re.compile(r"Limits:.*?(\d+)\s*-\s*(\d+)")
_SWITCH = re.compile(r"\[(on|off)\]")


def _amixer(card: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["amixer", "-c", str(card), *args],
        capture_output=True, text=True, timeout=10,
    )


def list_cards(proc_path: str = "/proc/asound/cards") -> list:
    try:
        text = Path(proc_path).read_text()
    except OSError:
        return []
    return [
        {"index": int(m.group(1)), "id": m.group(2)}
        for m in _CARD_LINE.finditer(text)
    ]


def get_controls(card: str) -> dict:
    out = {}
    for name, fallback_max in CONTROLS.items():
        result = _amixer(card, "sget", name)
        if result.returncode != 0:
            continue
        value = _VALUE.search(result.stdout)
        limits = _LIMITS.search(result.stdout)
        switch = _SWITCH.search(result.stdout)
        if value:
            out[name] = {
                "value": int(value.group(1)),
                "max": int(limits.group(2)) if limits else fallback_max,
                "switch": switch.group(1) if switch else None,
            }
    return out


def set_control(card: str, control: str, value) -> dict:
    if control not in CONTROLS:
        raise ValueError(f"control {control!r} is not adjustable here")
    value = max(0, min(int(value), CONTROLS[control]))
    args = [str(value)]
    if control == "Capture":
        args.append("cap")  # keep the capture switch enabled
    result = _amixer(card, "sset", control, *args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "amixer failed")
    return {"control": control, "value": value}


def apply_recipe(card: str) -> dict:
    applied, failed = [], []
    for control, args in RECIPE:
        result = _amixer(card, "sset", control, *args)
        (applied if result.returncode == 0 else failed).append(control)
    return {"applied": applied, "failed": failed}


def store() -> dict:
    """Persist mixer state across reboots.

    Writing /var/lib/alsa/asound.state needs root, so after a plain attempt
    we try non-interactive sudo (``sudo -n`` — succeeds on stock Raspberry
    Pi OS where the default user has passwordless sudo; a fixed command
    list, never a shell). Only if both fail do we hand back the command.
    """
    for command in (["alsactl", "store"], ["sudo", "-n", "alsactl", "store"]):
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=10
            )
        except OSError as exc:
            return {"ok": False, "hint": f"alsactl unavailable: {exc}"}
        if result.returncode == 0:
            return {"ok": True}
    return {
        "ok": False,
        "hint": "needs root — run on the device:  sudo alsactl store",
        "error": (result.stderr or "").strip()[:200],
    }
