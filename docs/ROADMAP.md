# Roadmap

Informed by the first real bring-up (Pi 4, Trixie, 2026-07): the hardest part
of deployment was audio-chain knowledge (mixer routing, gain calibration,
misleading level metrics), not the config file itself.

## V1.x — onboarding

- ✅ **`hermes-satellite setup` wizard** (shipped 2026-07-07/08 as the
  on-demand web wizard; field-hardened across two fresh installs):
  automate the entire audio bring-up —
  detect the HAT/overlay, apply the wm8960 mixer recipe, interactive gain
  calibration with a live level meter (target p99 30-70%), speaker test tone,
  `alsactl store`, write `config.yaml`, optionally install the systemd unit.
  Everything docs/hardware/pi4-respeaker-v1.md does by hand, guided.
- ✅ **`hermes-satellite doctor`** — shipped 2026-07-08: one-shot CLI health
  check (board vs profile, SPI node, ALSA card, onnxruntime pin, wake model
  files, Moonshine cache, voices, data dir, Hermes reachability); exit code 1
  on failure so scripts can gate on it. Same checks power the wizard's
  Status section (shared `doctor.py`).
- ✅ Earcons (wake chime / error tone) — shipped 2026-07-08.
- ✅ Follow-up conversation mode — shipped 2026-07-08.
- Wizard transcription-test section (exam finding: STT is the one stage
  the wizard never exercises; would also pre-warm the Moonshine cache).

## V2 — configuration & integration (architecture agreed 2026-07-07)

Configuration splits into two tiers with different solutions:

**0. Runtime settings layer (enabler for both tiers).** — ✅ shipped 2026-07-07 Config stays
read-only for the service; runtime-tweakable knobs (mute, volume, wake
threshold, voice, length_scale, LED brightness) get a thread-safe get/set
registry with apply semantics (live / component-reload / needs-restart) and
persist to an overlay `/var/lib/hermes-satellite/runtime.yaml` merged over
config.yaml at load — the sandboxed service writes only its own data dir.

**1. Daily knobs → Home Assistant via MQTT discovery** — ✅ shipped 2026-07-07 (outbound-only — no
listening ports, preserving the IoT-VLAN posture; see docs/networking.md):

- `switch`: mute (mirrors the HAT button, both directions)
- `number`: speaker volume, wake threshold, TTS length_scale, LED brightness
- `select`: TTS voice (populated from downloaded voices)
- `sensor`: pipeline state (idle/listening/thinking/speaking); availability
  topic → online/offline
- `event`/trigger: wake word detected — enables "pause media when addressed"

**2. Setup → on-demand web wizard, not a web service.** — ✅ shipped 2026-07-07
`hermes-satellite setup --web` starts a temporary token-protected server,
walks device selection → mixer calibration with a live level meter → voice
audition → wake threshold tuning with live scores → Hermes connection test,
writes config.yaml, and **exits**. Steady-state listening ports remain zero.
Reuses the proven guts: --ww-monitor, voices preview, wm8960-mixer.sh.

**Also V2:**
  (Follow-up conversation mode — shipped; see above.)
- ✅ SSE streaming from Hermes + sentence-chunked Piper synthesis —
  shipped 2026-07-08.

## V2+ — maybe

- Barge-in (wake word interrupts playback).
- Multi-satellite conventions (per-device session keys already exist).
