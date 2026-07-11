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
silence or `max_record_seconds`. With `stt.streaming` on, every captured
frame is also fed to a Moonshine streaming session as it arrives, so the
transcript is ready the moment capture ends instead of after a post-capture
transcription pass. Onset requires **90 ms of consecutive speech**
— a single loud frame is ignored, so clicks and the WM8960's output-stage pop
(~15 ms, full scale) can't open recording on their own. While muted, capture
returns empty and the wake loop drains frames without processing them.

Playback opens the output stream at the TTS engine's native rate
(`TTSEngine.sample_rate` — e.g. a Piper voice's 22050 Hz), so no resampling is
needed. `play()` holds until the audio has actually left the DAC —
PortAudio's `write()` returns once frames are merely *buffered*, and closing
an active stream discards the rest, which used to cut earcons short and,
worse, return control while the speaker was still audible. Because the mics
sit 5 cm from the speaker, the pipeline also waits a short settle
(`MIC_SETTLE_S`, 200 ms) after any playback before flushing the mic and
arming capture; without it, our own chime landed in the mic buffer *after*
the flush and the capture VAD opened on it — the field symptom was a
follow-up window that closed after ~1 s (`silence_ms`) instead of staying
open for `follow_up_seconds`.

Per-engine details: [wakeword.md](wakeword.md), [moonshine.md](moonshine.md),
[piper.md](piper.md).

Every turn ends with one INFO log line for latency work —
`turn timing: capture 2.4s · stt 1.2s · first-reply 3.1s · first-audio 4.0s
· total 9.8s` (stage durations for capture/stt; the rest relative to the
start of recording). It's the feedback loop for tuning `silence_ms` and for
measuring streaming STT.

## Earcons, follow-up mode & barge-in

Three conversational conveniences layer on the pipeline (`core/pipeline.py`):

- **Earcons** (`core/earcons.py`): short synthesized tones — a rising chime
  the instant the wake word fires (so you know it heard you without watching
  the LEDs) and a falling tone on error. No bundled audio; tones are generated
  at runtime and played through the same `AudioSink` as speech. `earcons:
  {enabled, volume}` in config.
- **Follow-up mode** (`conversation.follow_up`): after a reply, capture
  re-opens for `follow_up_seconds` so a continuation needs no wake word. A
  follow-up turn is modeled as a **virtual wake** — it dispatches the same
  `WAKE_DETECTED` and reuses the `IDLE→WAKE→RECORD` transitions, so the state
  machine and LEDs need no special cases. A soft "listening" chime marks each
  re-open; `conversation.max_turns` caps consecutive follow-ups. The mic is
  flushed after each chime so the tone's own echo can't false-trigger capture.
- **Barge-in** (`conversation.barge_in`, default off): while the assistant
  speaks, a listener thread keeps wake detection running on the shared mic
  (nobody else reads it during playback). A detection sets a cancel event
  that playback checks every ~100 ms write chunk, aborting the output stream
  — silence within a beat — and the pipeline starts a fresh turn (wake
  chime, flush, capture with the normal timeout; the turn counter resets
  since a barge is a new conversation). The listener is *cancelled*, never
  `stop()`ed, at the end of each playback, so the detector stays usable.
  Physics caveat: the speaker sits 5 cm from the mics, so your wake word
  competes with the assistant's own voice — moderate playback volume helps,
  and there is no echo cancellation. In streaming mode a barge also tells
  the synth-ahead thread to abandon the rest of the reply.
  Two stop-flavored refinements: `conversation.barge_model_path` points at a
  custom-trained model for a dedicated interrupt phrase (e.g. "jarvis stop",
  trained per [wakeword.md](wakeword.md) §4; threshold via
  `barge_threshold`) — when set, hearing it **stops the reply and returns to
  idle** instead of opening a new turn. And independent of barge-in, a
  captured transcript that is exactly a stop command ("stop", "never mind",
  "cancel", …) ends the conversation with a confirmation blip instead of a
  Hermes round-trip — so "hey jarvis … stop" already silences a reply
  mid-follow-up with no custom model at all.

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
   already here from the wizard, and **if you ran the wizard's Transcription
   test, so is the Moonshine model** (it downloads into `{data_dir}/cache`) —
   then only the `chown` below is needed. Otherwise pre-seed the model so
   first start needs no download. **Note:** `~/.cache/moonshine_voice` only exists
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

### Day-2 operations

Two helper scripts wrap the routine chores on a deployed satellite —
each stops the daemon only if it is running and restarts it only if it
was (symlink them into your home directory for convenience):

```bash
ln -s /opt/hermes-satellite/scripts/configure-satellite.sh ~/configure-satellite
ln -s /opt/hermes-satellite/scripts/update-satellite.sh ~/update-satellite
```

* `~/configure-satellite` — runs the [setup wizard](setup-wizard.md)
  against the live `/etc/hermes-satellite/config.yaml`.
* `hermes-satellite doctor` — one-shot health check from a shell (no
  wizard needed): board vs profile, SPI, ALSA card, onnxruntime pin, model
  files, Hermes reachability. Exit code 1 if anything failed. Read-only and
  never opens the microphone, so it's safe to run whether or not the
  service is running. On a deployed satellite:
  ```bash
  sudo /opt/hermes-satellite/.venv/bin/hermes-satellite doctor --config /etc/hermes-satellite/config.yaml
  ```
* `~/update-satellite [branch]` — pulls the latest revision into `/opt`,
  reinstalls into the venv (auto-detects the `[pi4]`/`[pi5]` extra from
  the board, so new dependencies land too), and shows the service status
  after restart. No-op if already at the latest revision.
  **Canary pattern:** with a branch name it switches the satellite to that
  branch — put one device on a feature branch (`~/update-satellite latency`)
  while the rest stay on main; it keeps updating along its branch until you
  bring it home with `~/update-satellite main`.

---

> ### ✅ All done here? Your satellite is complete
>
> Final rites:
>
> 1. `sudo /opt/hermes-satellite/.venv/bin/hermes-satellite doctor --config /etc/hermes-satellite/config.yaml`
>    — everything ✓, exit 0.
> 2. **Power-plug reboot test**: pull the plug, plug it back in, say the
>    wake word once it settles. If that works, everything works.
> 3. Optional: surface it in Home Assistant —
>    [home-assistant.md](home-assistant.md) (MQTT is also a wizard section).

---

## Testing / verification

- `pytest` — state machine, LED controller, Hermes client, config.
- `hermes-satellite --demo --hardware-profile mock --config config.example.yaml`
  — full pipeline with mocks; Enter toggles mute.
- On hardware: set the real `hardware_profile`, `leds.backend: apa102`, run
  `--demo` to see the LEDs animate and the button mute (still mock audio/agent).
