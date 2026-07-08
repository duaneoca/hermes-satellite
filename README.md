# hermes-satellite

A Python daemon that turns a Raspberry Pi + Seeed ReSpeaker 2‑Mic HAT into a
voice front‑end for a [Hermes](docs/hermes-api.md) agent backend.

```
openWakeWord wake word → VAD‑gated capture → Moonshine STT (on‑device)
   → HTTP POST to Hermes (OpenAI‑compatible) → Piper TTS → speaker
```

A single state machine (`IDLE → WAKE → RECORD → PROCESS → SPEAK → IDLE`) drives
the pipeline, and the HAT's 3 APA102 LEDs reflect the current state. The HAT
button toggles microphone mute.

## Status

All components are implemented:

| Component                         | Status                                     |
| --------------------------------- | ------------------------------------------ |
| Core state machine                | ✅ implemented                             |
| LED controller (APA102 + mock)    | ✅ implemented                             |
| Mute button (RPi.GPIO/lgpio/mock) | ✅ implemented                             |
| Hermes API client                 | ✅ implemented (non‑streaming)             |
| Config + hardware profiles        | ✅ implemented                             |
| Wake word (openWakeWord, default) | ✅ implemented + live-validated            |
| Wake word (Porcupine, optional)   | ✅ implemented (needs paid Picovoice key)  |
| Audio capture + VAD (ALSA)        | ✅ implemented (webrtcvad‑gated)           |
| STT (Moonshine)                   | ✅ implemented (moonshine‑voice 0.1.x)     |
| TTS (Piper)                       | ✅ implemented (both piper‑tts APIs)       |

Every backend has a mock counterpart, so the whole daemon runs via `--demo` on
any machine. On‑device validation (real mic/speaker/LEDs) still needs the Pi.

## Quick start (development, no hardware)

```bash
git clone https://github.com/duaneoca/hermes-satellite.git
cd hermes-satellite
python -m venv .venv && source .venv/bin/activate
pip install --upgrade pip setuptools wheel   # older pips can't editable-install
                                             # from pyproject.toml alone
pip install -e ".[dev]"
pytest
cp config.example.yaml config.yaml   # edit as needed
hermes-satellite --demo --hardware-profile mock --config config.example.yaml
```

Deploying to a Pi? Follow your board's guide instead:
[Pi 4 + HAT v1](docs/hardware/pi4-respeaker-v1.md) ·
[Pi 5 + HAT v2](docs/hardware/pi5-respeaker-v2.md) — they cover the codec
driver, SPI, groups, and install order.

`--demo` uses mock wakeword/audio/STT/TTS/LEDs and a canned agent reply, walking
the state machine through a full cycle. Press Enter to toggle mute.

## Hardware

Two hardware profiles are supported (set `hardware_profile` in config):

- `pi4-respeaker-v1` — Raspberry Pi 4 + ReSpeaker 2‑Mic HAT v1 (WM8960)
- `pi5-respeaker-v2` — Raspberry Pi 5 + ReSpeaker 2‑Mic HAT v2 (TLV320AIC3104)

See the per‑board setup guides under [`docs/hardware/`](docs/hardware/).

## Documentation

- [Architecture & implementation guide](docs/hermes-satellite.md)
- [Hermes API integration](docs/hermes-api.md)
- [Networking / IoT VLAN deployment](docs/networking.md)
- [Home Assistant integration (MQTT)](docs/home-assistant.md)
- [Wake word (openWakeWord)](docs/wakeword.md)
- [Porcupine backend (optional)](docs/porcupine.md)
- [Moonshine (STT)](docs/moonshine.md)
- [Piper (TTS)](docs/piper.md)
- Hardware: [Pi 4 + HAT v1](docs/hardware/pi4-respeaker-v1.md) ·
  [Pi 5 + HAT v2](docs/hardware/pi5-respeaker-v2.md) ·
  [Seeed software notes](docs/hardware/seeed-software.md)

## Running as a service

See [`systemd/hermes-satellite.service`](systemd/hermes-satellite.service) and
the "Running as a service" section of the
[implementation guide](docs/hermes-satellite.md).

## License

MIT. Bundles Seeed's APA102 driver (`vendor/apa102.py`, MIT).
