# Setup wizard (on-demand web UI)

The satellite runs **no resident web server** — that's a load-bearing
security property for an always-listening device on an IoT VLAN
([networking.md](networking.md)). Rich setup and deep tweaking happen through
a **temporary** wizard instead:

```bash
# stop the daemon first if it's running (they share the microphone):
sudo systemctl stop hermes-satellite      # or Ctrl-C an interactive run
hermes-satellite setup --config /path/to/config.yaml
```

On a deployed satellite, `scripts/configure-satellite.sh` wraps the whole
ceremony (stop daemon if running → wizard against the live `/etc` config →
restart only if it was running). Symlink it for convenience:

```bash
ln -s /opt/hermes-satellite/scripts/configure-satellite.sh ~/configure-satellite
```

It prints a URL like `http://jarvis-pi-4:8321/?token=Kx3v…` — open it from
any browser on the LAN. When you're done (Exit button, Ctrl-C, or 15 idle
minutes — `--idle-timeout-min`), the process exits and **no ports remain
open**.

## Security model

- **One-time token** (random per session) gates every request — this also
  defeats CSRF, since a hostile page can't know the token.
- No stored web credentials, no TLS-cert management, nothing to patch in
  steady state: the server exists only while an operator is using it.
- The wizard runs as *you* (not the sandboxed service), against the config
  file you point it at.

## Sections

1. **Status** — doctor checks: profile, SPI node, onnxruntime version
   (flags the broken 1.27.x), downloaded voices, Hermes `/health`.
2. **Audio devices** — pick input/output/channels from live device list;
   test tone button.
3. **Microphone level** — live meter (RMS + rolling p99; target p99
   30–70 %) **with mixer sliders right beside it**: pick the ALSA card,
   drag Capture/ADC PCM/Speaker while watching the meter, one-click
   "Apply WM8960 recipe" (the full routing fix from the Pi 4 guide),
   and Persist (`alsactl store`; falls back to printing the sudo
   command if it needs root). Only whitelisted controls are settable.
4. **Wake word** — live score monitor + detection counter; set the
   threshold from evidence. Model load takes a few seconds on a Pi:
   the page shows "starting" until scoring actually begins, and the
   HAT LEDs light in the listening color while the test is live —
   don't say the phrase until you see them.
5. **Transcription** — record one utterance and see what the on-device
   STT heard, with capture/transcribe timings. First use downloads the
   Moonshine model **into the service's cache location**
   (`{data_dir}/cache`), so a wizard-first install needs no separate
   pre-seed step.
6. **Voice** — full catalog + downloaded voices, on-device preview with
   speaker/pace knobs (downloads on demand).
7. **Hermes** — health + authenticated round-trip chat test.
8. **Conversation & sounds** — streamed replies (speak while the rest of
   the answer is still arriving), follow-up mode (window length + max
   turns per conversation), and earcons (enable + volume).
9. **Home Assistant (MQTT)** — enable toggle, broker settings with a
   masked password (kept in `secrets.env`), and a live broker connection
   test that distinguishes unreachable / auth-refused / connected.
10. **Review & save** — collected changes are listed; **Save
   configuration** first copies your current config to a timestamped
   backup (`config.yaml.bak-YYYYMMDD-HHMMSS`) and then updates
   `config.yaml` in place. **Credentials are stripped from the yaml and
   written to a 0600 `secrets.env` beside it** — the same file the daemon
   reads interactively and systemd's `EnvironmentFile` reads when
   deployed. Restart the daemon to apply. (Comments in the config file
   are not preserved by the rewrite — they live on in the backup.)

Changes made in sections 2–8 apply to the wizard's live session immediately
(so previews/tests use them), and land in the review file at the end.
