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
   threshold from evidence.
5. **Voice** — full catalog + downloaded voices, on-device preview with
   speaker/pace knobs (downloads on demand).
6. **Hermes** — health + authenticated round-trip chat test.
7. **Review & save** — collected changes are written to
   `<config>.yaml.new` for *your* review, with the `mv` command printed.
   The wizard never rewrites your config behind your back.

Changes made in sections 2–6 apply to the wizard's live session immediately
(so previews/tests use them), and land in the review file at the end.
