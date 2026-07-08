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
  button.py     # Mute state + HAT button watcher (lgpio / legacy RPi.GPIO / mock)
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
backend is chosen from the hardware profile — `LgpioButton` (Pi 4 and Pi 5;
RPi.GPIO edge detection is broken on kernels ≥ ~6.6), `RpiGpioButton` (legacy
kernels only), or `MockButton` (Enter on stdin). Each press calls
`Mute.toggle()`. The wakeword detector and audio capture receive `is_muted` and
ignore audio while it returns True.

## Configuration

A single `config.yaml` (see `config.example.yaml`). Loaded and validated by
`config.py` into typed dataclasses. Notes:

- `hardware_profile` selects SPI numbering, GPIO backend and LED count defaults.
  Override on the CLI with `--hardware-profile`.
- **Credentials never live in `config.yaml`.** They come from (highest
  precedence first): real environment variables (`HERMES_API_KEY`,
  `HERMES_SESSION_KEY`, `PORCUPINE_ACCESS_KEY`, `MQTT_PASSWORD`), then a
  `secrets.env` file **next to the config** (0600; the wizard's Save writes
  it automatically, stripping any keys out of the yaml), then — discouraged —
  values in the yaml itself. Deployed, the same `secrets.env` is what the
  systemd unit's `EnvironmentFile` reads.
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

## Logging & SD-card wear

- The daemon logs via Python logging to stderr → **journald** under
  systemd. Set `log_level: WARNING` in config for a deployed satellite —
  errors only, near-zero steady-state writes (`--log-level` on the CLI
  overrides for a debugging session).
- The chatty outputs (`--ww-monitor` score/level lines, `--demo`
  transitions at INFO) are interactive tools, not service behavior.
- Raspberry Pi OS defaults journald to **volatile (RAM) storage** unless
  `/var/log/journal` exists — check with `journalctl --disk-usage`. If
  you enabled persistent journals, cap them (`SystemMaxUse=64M` in
  `/etc/systemd/journald.conf`) to protect the SD card.

## Network requirements

The daemon opens **no listening ports**; all connections are outbound, and
steady-state it needs exactly one flow: TCP to the Hermes host on 8642. See
[networking.md](networking.md) for the full traffic profile, IoT VLAN / firewall
guidance, Wi-Fi (WPA3) caveats, and fully-offline install instructions.

## Running as a service

The deployed daemon runs as a dedicated **`hermes-sat` system user** (named
to avoid confusion with the Hermes *server*) whose entire
privilege is "may touch sound, SPI and GPIO." Ownership principle: *the
service can write only its own data directory.*

| Path | Owner / mode | Service access | Holds |
| ---- | ------------ | -------------- | ----- |
| `/opt/hermes-satellite` | root | read-only | code + venv |
| `/etc/hermes-satellite/config.yaml` | root:hermes-sat 640 | read-only | configuration |
| `/etc/hermes-satellite/secrets.env` | root:root 600 | none (systemd reads it) | API keys |
| `/var/lib/hermes-satellite` | hermes-sat:hermes-sat | **read-write** | models, caches, lgpio pipes |

The unit also enables systemd sandboxing (`ProtectSystem=strict`,
`ProtectHome`, `PrivateTmp`, `NoNewPrivileges`) — appropriate hygiene for an
always-listening device. Your development clone (e.g. `~/git/hermes-satellite`)
stays your user's and is unrelated to the deployed copy.

1. Create the service user, then install to `/opt` (root-owned, venv from
   the same Python that worked interactively — on Trixie that's uv's 3.11).

   > **Trixie/uv gotcha:** uv normally installs its Pythons under *your*
   > home directory, but the unit sets `ProtectHome=true` — the service
   > would be unable to read its own interpreter and fail at startup.
   > `UV_PYTHON_INSTALL_DIR` puts the interpreter inside `/opt` instead.

   Note: `hermes-sat` needs **no** shell environment, PATH, or uv setup —
   uv is bootstrap tooling for the admin; the unit runs the venv's python by
   absolute path.
   ```bash
   sudo useradd -r -s /usr/sbin/nologin -G spi,audio,gpio hermes-sat
   sudo git clone https://github.com/duaneoca/hermes-satellite /opt/hermes-satellite
   sudo UV_PYTHON_INSTALL_DIR=/opt/hermes-satellite/python "$HOME/.local/bin/uv" python install 3.11
   sudo UV_PYTHON_INSTALL_DIR=/opt/hermes-satellite/python "$HOME/.local/bin/uv" venv --seed --python 3.11 /opt/hermes-satellite/.venv
   sudo /opt/hermes-satellite/.venv/bin/pip install --upgrade pip setuptools wheel
   sudo /opt/hermes-satellite/.venv/bin/pip install -e "/opt/hermes-satellite[pi4]"  # or [pi5]
   # Pre-seed the openWakeWord model files INTO THIS venv — the wizard only
   # downloaded them into your interactive clone's venv, and the sandboxed
   # service cannot write to /opt to fetch them itself:
   sudo /opt/hermes-satellite/.venv/bin/python -c "import openwakeword.utils; openwakeword.utils.download_models(['hey_jarvis'])"
   # sanity: the interpreter must NOT live under /home
   readlink -f /opt/hermes-satellite/.venv/bin/python   # expect /opt/hermes-satellite/python/...
   ```
   (On Bookworm, plain `python3 -m venv` with the system 3.11 avoids all of
   this.)
2. Config and secrets (admin-owned, service-readable where needed). The
   wizard's Save already produced both files next to your clone's config —
   a credential-free `config.yaml` and a 0600 `secrets.env`:
   ```bash
   sudo mkdir -p /etc/hermes-satellite
   sudo cp config.yaml /etc/hermes-satellite/config.yaml       # your tuned config
   sudo chown root:hermes-sat /etc/hermes-satellite/config.yaml
   sudo chmod 640 /etc/hermes-satellite/config.yaml
   sudo cp secrets.env /etc/hermes-satellite/secrets.env
   sudo chown root:root /etc/hermes-satellite/secrets.env
   sudo chmod 600 /etc/hermes-satellite/secrets.env
   ```
   (Add `HERMES_SESSION_KEY=...` to secrets.env if you prefer it out of the
   yaml too; it's a scoping label rather than a credential.)
3. Data directory (the only place the service writes) — the Piper voice is
   already here from the wizard; pre-seed the Moonshine STT model so first
   start needs no download. **Note:** `~/.cache/moonshine_voice` only exists
   if you ever ran the daemon interactively — on the wizard-first path it
   doesn't (the wizard doesn't exercise STT), so download straight into the
   service location:
   ```bash
   sudo mkdir -p /var/lib/hermes-satellite/cache
   sudo XDG_CACHE_HOME=/var/lib/hermes-satellite/cache /opt/hermes-satellite/.venv/bin/python -c "import moonshine_voice as mv; mv.get_model_for_language('en', mv.ModelArch.BASE)"
   # (or, if ~/.cache/moonshine_voice exists from interactive runs:
   #  sudo cp -r ~/.cache/moonshine_voice /var/lib/hermes-satellite/cache/ )
   sudo chown -R hermes-sat:hermes-sat /var/lib/hermes-satellite
   ```
4. Install and start the unit:
   ```bash
   sudo cp /opt/hermes-satellite/systemd/hermes-satellite.service /etc/systemd/system/
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
