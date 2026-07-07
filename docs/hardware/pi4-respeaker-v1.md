# Raspberry Pi 4 + ReSpeaker 2-Mic HAT v1

Profile: `hardware_profile: pi4-respeaker-v1`

| Aspect        | Value                                                    |
| ------------- | -------------------------------------------------------- |
| Audio codec   | WM8960 (via `seeed-voicecard` DKMS driver)               |
| LEDs          | 3× APA102 on SPI0, CE1 → `/dev/spidev0.1`                |
| Button        | GPIO17 (BCM), active‑low pull‑up, via `RPi.GPIO`         |
| GPIO backend  | `RPi.GPIO`                                               |

See also [seeed-software.md](seeed-software.md) for driver/kernel caveats.

## 1. Audio codec (WM8960)

**You do NOT need an old Raspberry Pi OS**, and on current kernels you may not
need any out-of-tree driver either. Two routes, best first:

### Option A (recommended): the kernel's built-in overlay — no driver install

The Raspberry Pi kernel ships a `wm8960-soundcard` overlay (present in the
rpi 6.12/6.15/6.17+ trees; nominally for the Waveshare WM8960 HAT, but the
ReSpeaker 2-Mic v1 uses the same codec wiring —
[respeaker/seeed-voicecard#281](https://github.com/respeaker/seeed-voicecard/issues/281)).
No DKMS module, nothing to rebuild after kernel upgrades:

```bash
# keep the ALSA card name the Seeed tooling/docs expect:
echo "dtoverlay=wm8960-soundcard,alsaname=seeed2micvoicec" | sudo tee -a /boot/firmware/config.txt
sudo reboot
```

After reboot the card should appear in `arecord -l`. The wm8960 driver's mixer
defaults are conservative — if capture is silent or playback muted, open
`alsamixer -c seeed2micvoicec` and raise `Capture` / unmute `Headphone` &
`Speaker`, then persist with `sudo alsactl store`.

### Option B (fallback): Seeed's out-of-tree driver, kernel-matched branch

Use this if Option A misbehaves (e.g. missing mixer routing for your unit) or
on kernels older than the built-in overlay. The
[HinTak fork](https://github.com/HinTak/seeed-voicecard) maintains a branch
per kernel — **clone the branch matching `uname -r`'s major.minor**:

```bash
uname -r                   # e.g. 6.12.47+rpt-rpi-v8  ->  branch v6.12
sudo apt-get update && sudo apt-get install -y git dkms
git clone -b v6.12 https://github.com/HinTak/seeed-voicecard   # match uname -r!
cd seeed-voicecard
sudo ./install.sh          # DKMS builds the module; reboot afterwards
sudo reboot
```

> Branch availability lags new kernels (v6.14 was the newest at the time of
> writing — check `git ls-remote --heads` if unsure). If your kernel is newer
> than every branch, use Option A. After an `apt full-upgrade` that bumps the
> kernel's major.minor, re-clone the matching branch and re-run `install.sh`;
> if audio disappears after an OS update, check `dkms status` first.

Verify after reboot:

```bash
arecord -l | grep -i seeed          # capture device present
aplay -l   | grep -i seeed          # playback device present
arecord -D plughw:seeed2micvoicec -f S16_LE -r 16000 -c 1 test.wav   # Ctrl-C to stop
aplay  -D plughw:seeed2micvoicec test.wav
```

### Audio device config: needed or not?

Usually **not**: leave `audio.input_device` / `audio.output_device` as `null`
and the system default device is used. That's correct when the seeed card is
the default (or only) sound card. Make it the default system-wide by creating
`/etc/asound.conf`:

```
defaults.pcm.card seeed2micvoicec
defaults.ctl.card seeed2micvoicec
```

If you do need to pin devices explicitly (e.g. HDMI keeps grabbing playback),
the values are **sounddevice (PortAudio) integer indices — NOT the card
numbers from `arecord -l`**. List them from inside the project venv:

```bash
python -c "import sounddevice as sd; print(sd.query_devices())"
#   0 bcm2835 Headphones: - (hw:0,0), ALSA (0 in, 8 out)
# > 1 seeed-2mic-voicecard: ... (hw:1,0), ALSA (2 in, 2 out)
#   2 default, ALSA (2 in, 2 out)
```

and use the leading index:

```yaml
audio:
  input_device: 1      # seeed capture (from the list above)
  output_device: 1     # seeed playback
```

> The seeed card usually only opens in **stereo**. If mono capture fails, set
> `audio.input_channels: 2` — the daemon then uses channel 0 (the left mic).
> Set mic capture volume with `alsamixer -c seeed2micvoicec` (unmuted, ~80%,
> not clipping).

## 2. Enable SPI (for the LEDs)

```bash
sudo raspi-config    # Interface Options -> SPI -> Enable
sudo reboot
ls -l /dev/spidev0.*  # expect /dev/spidev0.0 and /dev/spidev0.1
```

The HAT's APA102 LEDs are on **CE1** → `/dev/spidev0.1`. The `pi4-respeaker-v1`
profile defaults `leds.spi_bus: 0`, `leds.spi_device: 1` accordingly.

## 3. Button & group membership

GPIO17, handled by `RPi.GPIO` (installed via the `[pi4]` extra). The user
running the daemon needs the `gpio`, `spi`, and `audio` groups. Check and fix:

```bash
id -nG                 # lists the current user's groups
sudo usermod -aG gpio,spi,audio $USER
# group changes take effect on next login:
exit                   # then ssh back in, and re-check with: id -nG
```

(The systemd unit's service user needs the same groups — the service setup in
[hermes-satellite.md](../hermes-satellite.md#running-as-a-service) creates it
with them.)

## 4. Install & run

> **Python version check first.** The `[pi4]` extra needs **Python ≤ 3.11**:
> `tflite-runtime` (pulled in by openwakeword on Linux) publishes aarch64
> wheels only up to cp311, and `RPi.GPIO` 0.7.1 does not compile on 3.13.
>
> - **Bookworm** (system Python 3.11): use the system Python as below.
> - **Trixie / Debian 13** (system Python 3.13): create the venv from a
>   [uv](https://docs.astral.sh/uv/)-managed Python 3.11 instead:
>
>   ```bash
>   curl -LsSf https://astral.sh/uv/install.sh | sh   # installs to ~/.local/bin
>   uv python install 3.11
>   uv venv --seed --python 3.11 .venv     # --seed puts pip in the venv
>   ```
>
>   then continue with `source .venv/bin/activate` and the pip steps below.

```bash
git clone https://github.com/duaneoca/hermes-satellite.git
cd hermes-satellite
python -m venv .venv && source .venv/bin/activate   # Bookworm; Trixie: see note above
pip install --upgrade pip setuptools wheel   # REQUIRED on Raspberry Pi OS:
    # its bundled pip is too old for pyproject.toml editable installs and
    # fails with: 'File "setup.py" not found ... editable mode currently
    # requires a setup.py based build.'
pip install -e ".[pi4]"
cp config.example.yaml config.yaml     # set hardware_profile: pi4-respeaker-v1
# LED + button smoke test (audio/agent still mock):
hermes-satellite --demo --config config.yaml
```

Expect the 3 LEDs to breathe/animate and the HAT button to toggle the red MUTED
state. Then set the wake word model and the Piper voice (see
[wakeword.md](../wakeword.md), [piper.md](../piper.md)) and run without
`--demo` for the full pipeline.

## Troubleshooting

- **No `seeed` ALSA card** after reboot → the DKMS module failed to build for
  your kernel; check `dkms status` and the HinTak fork's issues for your kernel
  version.
- **`/dev/spidev0.1` missing** → SPI not enabled, or an overlay is claiming CE1;
  check `/boot/firmware/config.txt`.
- **LED permission denied** → add the user to `spi` (and re‑login), or check
  `/dev/spidev*` group ownership.
