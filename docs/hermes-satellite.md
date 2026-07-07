# hermes-satellite — architecture & implementation guide

## Overview

`hermes-satellite` is a threaded Python daemon. The **main thread** runs the
pipeline (all the blocking work: wake detection, capture, STT, HTTP, TTS,
playback). Two helper daemon threads run alongside:

- the **LED animation thread** (renders the current state at ~30 FPS), and
- the **button watcher** (toggles mute on a HAT button press).

```
                 ┌──────────────── main thread ───────────────┐
   wake word ──▶ capture ──▶ STT ──▶ Hermes ──▶ TTS ──▶ playback
       │            │                                     │
       └── gated by is_muted() ──┐                        │
                                 ▼                         ▼
   StateMachine.dispatch(event) ─┴─ on_transition ─▶ LEDController (thread)
                                                     ▲
   HAT button ─▶ Mute.toggle() ─▶ on_mute ──────────┘   (button thread)
```

## Module layout

```
src/hermes_satellite/
  config.py     # YAML schema + loader + env overrides
  platform.py   # hardware profiles (SPI numbering, GPIO backend, LED count)
  button.py     # Mute state + HAT button watcher (RPi.GPIO / lgpio / mock)
  app.py        # SatelliteApp: builds everything, owns lifecycle & LED glue
  cli.py        # argument parsing / entry point
  core/
    states.py   # State enum
    events.py   # Event enum + StateMachine (transition table, observers)
    pipeline.py # orchestration loop
  leds/         # LEDState, LEDController ABC, AnimatedLEDController, backends
  hermes/       # AgentClient ABC + HermesClient + mock
  wakeword/     # WakeWordDetector ABC + Porcupine + mock
  audio/        # AudioSource/Sink ABCs + shared MicStream + ALSA + webrtcvad + mock
  stt/          # STTEngine ABC + Moonshine + mock
  tts/          # TTSEngine ABC + Piper + mock
vendor/apa102.py  # Seeed APA102 SPI driver (vendored)
```

## The state machine

`core/states.py` and `core/events.py`. States: `IDLE, WAKE, RECORD, PROCESS,
SPEAK, ERROR`. The transition table is explicit:

| From    | Event               | To      |
| ------- | ------------------- | ------- |
| IDLE    | `WAKE_DETECTED`     | WAKE    |
| WAKE    | `RECORDING_STARTED` | RECORD  |
| RECORD  | `SPEECH_CAPTURED`   | PROCESS |
| PROCESS | `RESPONSE_READY`    | SPEAK   |
| SPEAK   | `PLAYBACK_DONE`     | IDLE    |
| *any*   | `ERROR`             | ERROR   |
| *any*   | `RESET`             | IDLE    |

`RESET` doubles as the "woke but heard nothing" abort back to IDLE. The machine
is thread‑safe and notifies subscribers on every change. It knows nothing about
LEDs — `app.py` subscribes and maps `State → LEDState`.

## LEDs

Two abstractions (`leds/base.py`):

- **`LEDBackend`** writes raw RGB frames (`APA102Backend` or `MockLEDBackend`).
- **`LEDController`** takes high‑level `LEDState` changes and animates them.

`AnimatedLEDController` (`leds/controller.py`) owns the animation thread and the
patterns (breathing idle, spinner while processing, pulsing error/speaking,
solid otherwise), so hardware and mock look identical. `LEDState` values: `IDLE,
WAKE, RECORDING, PROCESSING, SPEAKING, MUTED, ERROR, OFF`.

Mute is an **input gate, not a pipeline state**: while muted the machine stays
IDLE and the LEDs show `MUTED`; the `on_mute` handler restores the state colour
on unmute.

## Mute button

`button.py`. `Mute` holds a thread‑safe flag with change listeners. The button
backend is chosen from the hardware profile — `RpiGpioButton` (Pi 4),
`LgpioButton` (Pi 5), or `MockButton` (Enter on stdin). Each press calls
`Mute.toggle()`. The wakeword detector and audio capture receive `is_muted` and
ignore audio while it returns True.

## Configuration

A single `config.yaml` (see `config.example.yaml`). Loaded and validated by
`config.py` into typed dataclasses. Notes:

- `hardware_profile` selects SPI numbering, GPIO backend and LED count defaults.
  Override on the CLI with `--hardware-profile`.
- Secrets (`hermes.api_key`, `hermes.session_key`, `wakeword.access_key`) may be
  left blank and supplied via env vars `HERMES_API_KEY`, `HERMES_SESSION_KEY`,
  `PORCUPINE_ACCESS_KEY`, which **override** file values.
- `leds.spi_bus` / `leds.spi_device` default from the profile; override if your
  kernel enumerates SPI differently (common on Pi 5 — see the Pi 5 guide).

## Swapping implementations

STT and LEDs (and, by the same pattern, wakeword/audio/TTS/Hermes) are chosen by
a `backend` name in config and built by a factory (`build_stt`,
`build_led_controller`, …). To add a new STT engine: implement
`stt.base.STTEngine`, add a branch to `stt/__init__.py:build_stt`, and set
`stt.backend` in config. No other code changes.

## Audio flow details

Wake detection and capture share a single `MicStream`
(`audio/mic.py`, `sounddevice`/PortAudio): opening and closing the device
between pipeline stages would drop the start of speech. The stream delivers
16 kHz int16 mono; if the codec only opens in stereo set
`audio.input_channels: 2` and channel 0 (left mic) is extracted.

Capture (`audio/alsa_backend.py`) is VAD-gated by webrtcvad in 30 ms frames:
wait up to `speech_timeout_seconds` for onset (keeping a 300 ms pre-roll so the
first syllable isn't clipped), then record until `silence_ms` of trailing
silence or `max_record_seconds`. While muted, capture returns empty and the
wake loop drains frames without processing them.

Playback opens the output stream at the TTS engine's native rate
(`TTSEngine.sample_rate` — e.g. a Piper voice's 22050 Hz), so no resampling is
needed.

Per-engine details: [wakeword.md](wakeword.md), [moonshine.md](moonshine.md),
[piper.md](piper.md).

## Running as a service

1. Install to `/opt/hermes-satellite` with its own venv:
   ```bash
   sudo mkdir -p /opt/hermes-satellite && sudo chown $USER /opt/hermes-satellite
   python -m venv /opt/hermes-satellite/.venv
   /opt/hermes-satellite/.venv/bin/pip install --upgrade pip setuptools wheel
   /opt/hermes-satellite/.venv/bin/pip install -e .           # add [pi4] or [pi5]
   ```
2. Put config at `/etc/hermes-satellite/config.yaml` and (optionally) secrets in
   `/etc/hermes-satellite/secrets.env`.
3. Create the service user and install the unit:
   ```bash
   sudo useradd -r -G spi,audio,gpio hermes
   sudo cp systemd/hermes-satellite.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now hermes-satellite
   sudo systemctl status hermes-satellite
   journalctl -u hermes-satellite -f
   ```

## Testing / verification

- `pytest` — state machine, LED controller, Hermes client, config.
- `hermes-satellite --demo --hardware-profile mock --config config.example.yaml`
  — full pipeline with mocks; Enter toggles mute.
- On hardware: set the real `hardware_profile`, `leds.backend: apa102`, run
  `--demo` to see the LEDs animate and the button mute (still mock audio/agent).
