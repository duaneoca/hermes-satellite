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

Set the ALSA device indices you find into config (`audio.input_device` /
`audio.output_device`), or leave `null` to use the ALSA default (configure it in
`/etc/asound.conf` / `~/.asoundrc`).

> The seeed card usually only opens in **stereo**. If mono capture fails, set
> `audio.input_channels: 2` — the daemon then uses channel 0 (the left mic).

## 2. Enable SPI (for the LEDs)

```bash
sudo raspi-config    # Interface Options -> SPI -> Enable
sudo reboot
ls -l /dev/spidev0.*  # expect /dev/spidev0.0 and /dev/spidev0.1
```

The HAT's APA102 LEDs are on **CE1** → `/dev/spidev0.1`. The `pi4-respeaker-v1`
profile defaults `leds.spi_bus: 0`, `leds.spi_device: 1` accordingly.

## 3. Button

GPIO17, handled by `RPi.GPIO` (installed via the `[pi4]` extra). No extra setup;
ensure the service user is in the `gpio` group.

## 4. Install & run

```bash
python -m venv .venv && source .venv/bin/activate
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
