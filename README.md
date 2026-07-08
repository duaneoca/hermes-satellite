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

## 🛰 Setting up a satellite? Start here

1. **Flash Raspberry Pi OS** (64-bit; Trixie works — the guides say what to
   do about its Python). Pick a good hostname: it becomes the device's name
   in Home Assistant and its memory scope on Hermes (room-based names like
   `jarvis-kitchen` age well).
2. **Follow your board's guide, top to bottom** — it covers the audio codec,
   SPI, groups, and install order:
   - **[Raspberry Pi 4 + ReSpeaker 2-Mic HAT v1 →](docs/hardware/pi4-respeaker-v1.md)**
   - **[Raspberry Pi 5 + ReSpeaker 2-Mic HAT v2 →](docs/hardware/pi5-respeaker-v2.md)**
3. **Run the setup wizard** for everything audio — mic calibration with a
   live meter, wake-word tuning, voice audition, Hermes connection test:
   `hermes-satellite setup` ([guide](docs/setup-wizard.md))
4. **Install it as a service** so it survives reboots:
   [Running as a service](docs/hermes-satellite.md#running-as-a-service)

On an IoT VLAN or segmented network? Read [networking.md](docs/networking.md)
first — the satellite needs no inbound ports and exactly one steady-state
outbound flow.

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

## Quick start for development (no hardware)

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
- [Setup wizard (on-demand web UI)](docs/setup-wizard.md)
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
