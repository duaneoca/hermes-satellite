"""Configuration schema and loader.

A single ``config.yaml`` drives the whole daemon. This module parses it into
typed dataclasses, fills SPI/LED defaults from the selected
:class:`~hermes_satellite.platform.HardwareProfile`, and applies environment
variable overrides for secrets so tokens need not live in the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from .platform import HardwareProfile, get_profile

# Environment variables that override the corresponding config fields.
ENV_HERMES_API_KEY = "HERMES_API_KEY"
ENV_HERMES_SESSION_KEY = "HERMES_SESSION_KEY"
ENV_PORCUPINE_ACCESS_KEY = "PORCUPINE_ACCESS_KEY"


class ConfigError(Exception):
    """Raised when configuration is missing required fields or malformed."""


@dataclass
class WakeWordConfig:
    # Engine: openwakeword (default, free) | porcupine (needs paid Picovoice key).
    backend: str = "openwakeword"
    # openwakeword: pretrained model name (hey_jarvis, alexa, ...) or path to a
    # custom .onnx/.tflite. porcupine: path to a .ppn model.
    model_path: str = ""

    # --- openwakeword tuning (see docs/wakeword.md for the tuning workflow) ---
    # Detection threshold on the 0-1 score. Calibrate with --ww-monitor.
    threshold: float = 0.5
    # Consecutive 80 ms frames that must exceed threshold before firing.
    # 1 = fire on a single frame. Higher cuts false accepts, may lower accepts.
    patience_frames: int = 1
    # Ignore further detections for this long after one fires.
    refractory_seconds: float = 2.0
    # Silero VAD gate: only accept detections when speech confidence exceeds
    # this. 0 disables. Cuts false accepts from non-speech noise.
    vad_threshold: float = 0.0
    # SpeexDSP noise suppression (Linux only; needs libspeexdsp).
    noise_suppression: bool = False
    # Optional second-stage verifier trained on the household's own voices
    # (openwakeword train_custom_verifier). Strongest false-accept fix.
    verifier_model_path: str = ""
    verifier_threshold: float = 0.1
    # onnx | tflite (tflite-runtime is Linux-only and flaky on newer Pythons).
    inference_framework: str = "onnx"

    # --- porcupine-only ---
    # Built-in keyword ("computer", "jarvis", ...) as alternative to model_path.
    builtin_keyword: str = ""
    access_key: str = ""
    sensitivity: float = 0.5


# Replies are spoken aloud; ask the agent for prose a TTS engine can read.
DEFAULT_SYSTEM_PROMPT = (
    "Your reply will be spoken aloud by a voice assistant. Respond in plain "
    "conversational prose only: no markdown, asterisks, bullet points, "
    "headers, tables, code blocks, or emojis. Be concise — one to three "
    "sentences — unless the user asks for detail."
)


@dataclass
class HermesConfig:
    host: str = "127.0.0.1"
    port: int = 8642
    api_key: str = ""
    session_key: str = ""
    model: str = "hermes-agent"
    timeout: float = 30.0
    # Sent as a system message before each utterance. Set to "" to disable
    # (e.g. if the persona/style is managed on the Hermes side).
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


@dataclass
class AudioConfig:
    backend: str = "alsa"
    input_device: Optional[int] = None
    output_device: Optional[int] = None
    sample_rate: int = 16000
    # Capture channels; the ReSpeaker seeed cards often only open in stereo.
    # Channel 0 (left mic) is used when > 1.
    input_channels: int = 1
    vad_aggressiveness: int = 2
    # End the utterance after this much trailing silence.
    silence_ms: int = 800
    # Give up if no speech starts within this window after the wake word.
    speech_timeout_seconds: float = 5.0
    # Hard cap on a single utterance.
    max_record_seconds: float = 10.0


@dataclass
class STTConfig:
    backend: str = "moonshine"
    model: str = "moonshine/base"
    language: str = "en"


@dataclass
class TTSConfig:
    backend: str = "piper"
    # Catalog voice name (e.g. en_GB-northern_english_male-medium), auto-
    # downloaded into voices_dir on first use. Browse with:
    #   hermes-satellite voices list --language en_GB
    voice: str = ""
    # Explicit path to a .onnx voice file; overrides `voice` when set.
    voice_path: str = ""
    voices_dir: str = "/var/lib/hermes-satellite/voices"
    # Multi-speaker voices (vctk, aru): which speaker to use.
    speaker_id: Optional[int] = None
    # Speaking pace: >1.0 slower/statelier, <1.0 brisker.
    length_scale: Optional[float] = None
    volume: float = 1.0


@dataclass
class LEDConfig:
    backend: str = "apa102"
    brightness: int = 8
    # Defaulted from the hardware profile when omitted.
    spi_bus: Optional[int] = None
    spi_device: Optional[int] = None


@dataclass
class Config:
    hardware_profile: str
    profile: HardwareProfile
    wakeword: WakeWordConfig
    hermes: HermesConfig = field(default_factory=HermesConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    leds: LEDConfig = field(default_factory=LEDConfig)
    # Daemon log level (DEBUG|INFO|WARNING|ERROR). WARNING keeps a deployed
    # satellite near-silent in the journal; the --log-level CLI flag overrides.
    log_level: str = "INFO"


def _section(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Config section '{key}' must be a mapping, got {type(value).__name__}")
    return value


def _build(data: dict, profile_override: Optional[str] = None) -> Config:
    """Build a :class:`Config` from an already-parsed mapping."""
    if not isinstance(data, dict):
        raise ConfigError("Top-level config must be a mapping")

    profile_name = profile_override or data.get("hardware_profile")
    if not profile_name:
        raise ConfigError("'hardware_profile' is required")
    try:
        profile = get_profile(profile_name)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    wake_data = _section(data, "wakeword")
    wakeword = WakeWordConfig(**_filter(wake_data, WakeWordConfig))
    wakeword.sensitivity = float(wakeword.sensitivity)
    wakeword.threshold = float(wakeword.threshold)
    backend = wakeword.backend.lower()
    if backend == "openwakeword":
        if not wakeword.model_path:
            raise ConfigError(
                "wakeword.model_path is required for openwakeword: a pretrained "
                "name (hey_jarvis, alexa, ...) or a path to a custom .onnx"
            )
    elif backend == "porcupine":
        if not (wakeword.model_path or wakeword.builtin_keyword):
            raise ConfigError(
                "porcupine requires wakeword.model_path (.ppn) or builtin_keyword"
            )
    else:
        raise ConfigError(
            f"Unknown wakeword.backend: {wakeword.backend!r} (openwakeword | porcupine)"
        )

    hermes = HermesConfig(**_filter(_section(data, "hermes"), HermesConfig))
    audio = AudioConfig(**_filter(_section(data, "audio"), AudioConfig))
    stt = STTConfig(**_filter(_section(data, "stt"), STTConfig))
    tts = TTSConfig(**_filter(_section(data, "tts"), TTSConfig))
    leds = LEDConfig(**_filter(_section(data, "leds"), LEDConfig))

    # Fill LED SPI defaults from the hardware profile.
    if leds.spi_bus is None:
        leds.spi_bus = profile.spi_bus
    if leds.spi_device is None:
        leds.spi_device = profile.spi_device

    log_level = str(data.get("log_level", "INFO")).upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigError(
            f"Invalid log_level: {data.get('log_level')!r} "
            "(DEBUG | INFO | WARNING | ERROR)"
        )

    config = Config(
        hardware_profile=profile.name,
        profile=profile,
        wakeword=wakeword,
        hermes=hermes,
        audio=audio,
        stt=stt,
        tts=tts,
        leds=leds,
        log_level=log_level,
    )
    _apply_env_overrides(config)
    return config


def _filter(data: dict, cls: type) -> dict[str, Any]:
    """Keep only keys that are fields of ``cls`` to give clear errors on typos."""
    valid = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    unknown = set(data) - valid
    if unknown:
        raise ConfigError(
            f"Unknown key(s) {sorted(unknown)} in section for {cls.__name__}"
        )
    return dict(data)


def _apply_env_overrides(config: Config) -> None:
    api_key = os.environ.get(ENV_HERMES_API_KEY)
    if api_key:
        config.hermes.api_key = api_key
    session_key = os.environ.get(ENV_HERMES_SESSION_KEY)
    if session_key:
        config.hermes.session_key = session_key
    access_key = os.environ.get(ENV_PORCUPINE_ACCESS_KEY)
    if access_key:
        config.wakeword.access_key = access_key


def load_config(path: str, profile_override: Optional[str] = None) -> Config:
    """Load and validate configuration from ``path``.

    ``profile_override`` (from ``--hardware-profile``) takes precedence over the
    ``hardware_profile`` key in the file.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    return _build(data, profile_override)
