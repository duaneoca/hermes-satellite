# Raspberry Pi 4 + ReSpeaker 2-Mic HAT v1

Profile: `hardware_profile: pi4-respeaker-v1`

| Aspect        | Value                                                    |
| ------------- | -------------------------------------------------------- |
| Audio codec   | WM8960 (via `seeed-voicecard` DKMS driver)               |
| LEDs          | 3× APA102 on SPI0, CE1 → `/dev/spidev0.1`                |
| Button        | GPIO17 (BCM), active‑low pull‑up, via `RPi.GPIO`         |
| GPIO backend  | `RPi.GPIO`                                               |

See also [seeed-software.md](seeed-software.md) for driver/kernel caveats.

## 1. Audio codec driver (WM8960)

The WM8960 needs Seeed's out‑of‑tree driver. On current Raspberry Pi OS kernels
use the [HinTak fork](https://github.com/HinTak/seeed-voicecard) (the upstream
`respeaker/seeed-voicecard` lags newer kernels):

```bash
sudo apt-get update && sudo apt-get install -y git dkms
git clone https://github.com/HinTak/seeed-voicecard
cd seeed-voicecard
sudo ./install.sh          # DKMS builds the module; reboot afterwards
sudo reboot
```

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

```bash
git clone https://github.com/duaneoca/hermes-satellite.git
cd hermes-satellite
python -m venv .venv && source .venv/bin/activate
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
