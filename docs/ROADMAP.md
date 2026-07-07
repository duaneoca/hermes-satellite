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

## V2 — integration & conversation

- **Home Assistant via MQTT discovery** (satellite stays outbound-only — no
  listening ports, preserving the IoT-VLAN posture; see docs/networking.md):
  - `switch`: mute (mirrors the HAT button)
  - `number`: wake threshold, speaker volume
  - `sensor`: pipeline state (idle/listening/thinking/speaking)
  - `event`/trigger: wake word detected — enables automations like "pause
    media when addressed"
  - availability topic → HA shows the satellite online/offline
- Follow-up conversation mode (re-open capture for N seconds after a reply,
  no wake word needed).
- SSE streaming from Hermes + sentence-chunked Piper synthesis (cut
  time-to-first-audio).

## V2+ — maybe

- Local web GUI for configuration. Tension: it adds a listening port to an
  always-on microphone device, which the current design deliberately avoids.
  Only if the setup wizard + HA integration prove insufficient; opt-in,
  LAN-bound, authenticated.
- Barge-in (wake word interrupts playback).
- Multi-satellite conventions (per-device session keys already exist).
