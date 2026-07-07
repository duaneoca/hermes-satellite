# Networking & IoT VLAN deployment

An always-listening microphone is exactly the class of device network
segmentation exists for. hermes-satellite is designed to live comfortably on a
locked-down IoT VLAN: it opens **no listening ports** — every connection is
outbound, and after initial setup it needs exactly one flow to function.

## Traffic profile

| Flow | When | Purpose |
| ---- | ---- | ------- |
| TCP → Hermes host : 8642 | **steady state** | the agent API — the only day-to-day need |
| DNS, NTP | steady state | name resolution, sane timestamps |
| HTTPS → github.com, pypi.org, files.pythonhosted.org | install / upgrade | git clone + pip |
| HTTPS → github.com (release assets) | first run | openWakeWord pretrained model auto-download |
| HTTPS → download.moonshine.ai | first run | Moonshine STT model auto-download |
| HTTPS → huggingface.co | setup | Piper voice download (manual step) |

No inbound rules are ever required. Nothing phones home: all HTTPS flows are
one-time artifact downloads, and audio never leaves the device — only the
transcribed text goes to your Hermes server.

## Firewall policy

Recommended: default-deny egress for the satellite, plus:

1. **Allow satellite → Hermes-host:8642 (TCP)** — permanent.
2. DNS + NTP per your VLAN's usual policy.
3. **Allow HTTPS egress temporarily during bring-up** (or pre-seed everything —
   see below), then remove it.

If Hermes runs next to Home Assistant on your trusted network, the rule shape
is identical to an HA-access rule — just a different destination port.

## Cross-VLAN gotchas

- **`hermes.host` must be routable from the IoT VLAN.** Use an IP address or a
  DNS name your IoT VLAN can resolve. **mDNS names (`something.local`) do not
  cross VLANs** without an mDNS reflector — prefer not to depend on one.
- The satellite itself never needs to be reached: no web UI, no SSH
  requirement at runtime (keep SSH management access per your own policy),
  nothing for other devices to discover.
- If DNS on the IoT VLAN is filtered, a static `hermes.host: <ip>` removes the
  dependency entirely.

## Wi-Fi: prefer Ethernet; avoid WPA3-only SSIDs on the Pi 4

The satellite is stationary and the Pi 4 has gigabit Ethernet — **wire it if
you can**. If you must use Wi-Fi:

- The Pi 4's CYW43455 radio + the firmware shipped by Raspberry Pi OS
  [does not reliably support WPA3-SAE](https://github.com/RPi-Distro/firmware-nonfree/issues/41)
  ([forum thread](https://forums.raspberrypi.com/viewtopic.php?t=370531)).
  Workarounds exist (swapped firmware blobs, custom wpa_supplicant builds) but
  can silently regress on OS upgrades — the wrong trade for a headless device.
- **Works:** a WPA2 SSID, or a WPA2/WPA3 transition-mode SSID (the Pi
  associates as WPA2). **Avoid:** WPA3-only SSIDs.
- A WPA2 IoT SSID mapped to the IoT VLAN is the natural fit.

## Fully offline / egress-denied installs

Everything the satellite downloads is a plain file — you can stage it all from
another machine and never open HTTPS egress:

- **Python packages:** on a machine with internet (same Python version/arch,
  or use `--platform`): `pip download -d wheelhouse ".[pi4]"`, copy
  `wheelhouse/` over, then `pip install --no-index --find-links wheelhouse -e ".[pi4]"`.
- **openWakeWord models:** copy `*.onnx`/`*.tflite` into
  `<venv>/lib/python*/site-packages/openwakeword/resources/models/`, or point
  `wakeword.model_path` at an absolute path.
- **Moonshine model:** copy the cache directory `~/.cache/moonshine_voice/`
  from a machine that has run it once.
- **Piper voice:** any path; set `tts.voice_path`.

## Failure signatures (segmented-network edition)

| Symptom | Likely cause |
| ------- | ------------ |
| First run hangs for minutes, then timeout tracebacks | model auto-download blocked by egress rules — allow HTTPS temporarily or pre-seed |
| `hermes` client timeouts, wake/STT all fine | satellite → Hermes:8642 rule missing, or `hermes.host` not routable from the VLAN |
| Works by IP, fails by hostname | IoT VLAN DNS filtered, or an mDNS `.local` name that doesn't cross VLANs |
| Won't join Wi-Fi at all | WPA3-only SSID — see above |
