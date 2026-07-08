# Roadmap

Informed by the first real bring-up (Pi 4, Trixie, 2026-07): the hardest part
of deployment was audio-chain knowledge (mixer routing, gain calibration,
misleading level metrics), not the config file itself.

## V1.x — onboarding

- **`hermes-satellite setup` wizard**: automate the entire audio bring-up —
  detect the HAT/overlay, apply the wm8960 mixer recipe, interactive gain
  calibration with a live level meter (target p99 30-70%), speaker test tone,
  `alsactl store`, write `config.yaml`, optionally install the systemd unit.
  Everything docs/hardware/pi4-respeaker-v1.md does by hand, guided.
- **`hermes-satellite doctor`**: one-shot health check — card present, mixer
  routing sane, capture level in range, SPI node, group membership, Hermes
  reachability, onnxruntime version (< 1.27), model files present.
- Earcons (wake chime / error tone) — audio feedback so users aren't
  dependent on seeing the LEDs.

## V2 — configuration & integration (architecture agreed 2026-07-07)

Configuration splits into two tiers with different solutions:

**0. Runtime settings layer (enabler for both tiers).** Config stays
read-only for the service; runtime-tweakable knobs (mute, volume, wake
threshold, voice, length_scale, LED brightness) get a thread-safe get/set
registry with apply semantics (live / component-reload / needs-restart) and
persist to an overlay `/var/lib/hermes-satellite/runtime.yaml` merged over
config.yaml at load — the sandboxed service writes only its own data dir.

**1. Daily knobs → Home Assistant via MQTT discovery** (outbound-only — no
listening ports, preserving the IoT-VLAN posture; see docs/networking.md):

- `switch`: mute (mirrors the HAT button, both directions)
- `number`: speaker volume, wake threshold, TTS length_scale, LED brightness
- `select`: TTS voice (populated from downloaded voices)
- `sensor`: pipeline state (idle/listening/thinking/speaking); availability
  topic → online/offline
- `event`/trigger: wake word detected — enables "pause media when addressed"

**2. Setup → on-demand web wizard, not a web service.**
`hermes-satellite setup --web` starts a temporary token-protected server,
walks device selection → mixer calibration with a live level meter → voice
audition → wake threshold tuning with live scores → Hermes connection test,
writes config.yaml, and **exits**. Steady-state listening ports remain zero.
Reuses the proven guts: --ww-monitor, voices preview, wm8960-mixer.sh.

**Also V2:**
- Follow-up conversation mode (re-open capture for N seconds after a reply,
  no wake word needed).
- SSE streaming from Hermes + sentence-chunked Piper synthesis (cut
  time-to-first-audio).

## V2+ — maybe

- Barge-in (wake word interrupts playback).
- Multi-satellite conventions (per-device session keys already exist).
