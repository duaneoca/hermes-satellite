# Raspberry Pi 5 + ReSpeaker 2-Mic HAT v2

Profile: `hardware_profile: pi5-respeaker-v2`

| Aspect        | Value                                                       |
| ------------- | ----------------------------------------------------------- |
| Audio codec   | TLV320AIC3104 (via **device‑tree overlay**, not seeed‑voicecard) |
| LEDs          | 3× APA102 on SPI0 CE1 → `/dev/spidev0.1` *or* `/dev/spidev10.1` |
| Button        | GPIO17 (BCM), active‑low pull‑up, via `lgpio` (RP1)         |
| GPIO backend  | `lgpio`                                                     |

The Pi 5 uses the **RP1** I/O chip. Two consequences drive this profile:

1. `RPi.GPIO` does **not** work — we use `lgpio` for the button.
2. SPI may enumerate as `/dev/spidev10.x` instead of `/dev/spidev0.x`.

See also [seeed-software.md](seeed-software.md).

## 1. Audio codec (TLV320AIC3104)

The v2 HAT's codec is supported by a **device‑tree overlay** rather than the
DKMS `seeed-voicecard` driver used for v1. Follow Seeed's current
[ReSpeaker 2‑Mic HAT v2 wiki](https://wiki.seeedstudio.com/respeaker_2_mics_pi_hat_raspberry_v2/)
for the exact overlay for your OS. In outline:

```bash
# Obtain/compile the v2 DTS overlay per the Seeed wiki, then:
sudo dtc -@ -I dts -O dtb -o /boot/firmware/overlays/respeaker-2mic-v2.dtbo respeaker-2mic-v2.dts
# Enable it:
echo "dtoverlay=respeaker-2mic-v2" | sudo tee -a /boot/firmware/config.txt
sudo reboot
```

Verify:

```bash
aplay -l && arecord -l           # a card for the TLV320AIC3104 should appear
arecord -f S16_LE -r 16000 -c 1 test.wav   # against that card; Ctrl-C to stop
aplay test.wav
```

> The overlay name/source and card name depend on the current Seeed release —
> always cross‑check the wiki.

**Audio device config:** usually leave `audio.input_device` / `output_device`
as `null` (system default) and make the HAT the default card in
`/etc/asound.conf`. If you must pin devices, the values are **sounddevice
(PortAudio) integer indices, not `arecord -l` card numbers** — list them with
`python -c "import sounddevice as sd; print(sd.query_devices())"` from the
project venv and use the leading index. Full walkthrough with sample output:
[pi4-respeaker-v1.md](pi4-respeaker-v1.md#audio-device-config-needed-or-not)
(identical procedure on the Pi 5). If mono capture fails, set
`audio.input_channels: 2` — the daemon then uses channel 0 (the left mic).

## 2. Enable SPI and confirm the LED device node

```bash
sudo raspi-config     # Interface Options -> SPI -> Enable
sudo reboot
ls -l /dev/spidev*
```

The APA102 LEDs are on SPI0 **CE1**. On the Pi 5 this may appear as either
`/dev/spidev0.1` **or** `/dev/spidev10.1` depending on kernel/overlay. Set config
to match the node you actually have:

```yaml
leds:
  backend: apa102
  spi_bus: 0        # use 10 if you see /dev/spidev10.1 instead of /dev/spidev0.1
  spi_device: 1
```

(The profile defaults to `spi_bus: 0, spi_device: 1`; override `spi_bus: 10` if
needed.)

## 3. Button (lgpio on RP1)

`RPi.GPIO` is incompatible with the RP1; the `[pi5]` extra installs `lgpio`
instead, and the profile selects the `lgpio` button backend automatically.

The RP1 header enumerates as **`gpiochip0`** on current Raspberry Pi OS
(Bookworm); some earlier kernels used `gpiochip4`. `LgpioButton` tries `0` then
`4`. Confirm with:

```bash
gpioinfo | grep -i gpiochip        # from gpiod; identify the RP1 40-pin header
```

The daemon's user needs the `gpio`, `spi`, and `audio` groups. Check and fix:

```bash
id -nG                 # lists the current user's groups
sudo usermod -aG gpio,spi,audio $USER
exit                   # group changes take effect on next login; re-check
```

## 4. Install & run

> **Python version check first.** The `[pi5]` extra needs **Python ≤ 3.11**
> (`tflite-runtime`, pulled in by openwakeword, has no aarch64 wheels past
> cp311). On **Trixie / Debian 13** (system Python 3.13), create the venv from
> a uv-managed Python 3.11 — see the identical note in
> [pi4-respeaker-v1.md](pi4-respeaker-v1.md#4-install--run). Bookworm's
> system Python 3.11 is fine as-is.

```bash
sudo apt install -y libportaudio2   # PortAudio: required by sounddevice on Linux
    # (missing => 'OSError: PortAudio library not found' at first audio use)
git clone https://github.com/duaneoca/hermes-satellite.git
cd hermes-satellite
python -m venv .venv && source .venv/bin/activate   # Bookworm; Trixie: see note above
python -c 'import sys; sys.exit(sys.version_info >= (3,12))' || \
  { echo "Python 3.12+ venv — STOP: recreate it per the Trixie note above"; }
    # If that printed STOP, do not continue: the install will fail with
    # 'No matching distribution found for tflite-runtime'.
pip install --upgrade pip setuptools wheel   # Raspberry Pi OS pip is too old
    # for pyproject.toml editable installs ('File "setup.py" not found ...')
pip install -e ".[pi5]"
cp config.example.yaml config.yaml     # hardware_profile: pi5-respeaker-v2
hermes-satellite --demo --config config.yaml   # LED + button smoke test
```

Expect the 3 LEDs to animate and the button to toggle MUTED.

---

> ### ✅ All done here? Next step: the setup wizard
>
> ```bash
> hermes-satellite setup --config config.yaml
> ```
>
> It walks mic calibration, wake word choice + threshold, a transcription
> check, voice audition, Hermes connection and conversation settings, then
> writes your config — guide: [setup-wizard.md](../setup-wizard.md).
> (Prefer configuring by hand? [wakeword.md](../wakeword.md) and
> [piper.md](../piper.md), then run without `--demo`.)

---

## Troubleshooting

- **LEDs don't light** → wrong SPI node; try `spi_bus: 10`. Confirm the exact
  `/dev/spidev*.*` file exists.
- **Button does nothing / `gpiochip` open error** → wrong chip number for your
  kernel; check `gpioinfo` and adjust `LgpioButton._CHIPS` if your header isn't
  `gpiochip0`/`gpiochip4`.
- **No audio card** → the v2 overlay didn't load; re‑check the Seeed wiki overlay
  for your OS release and `config.txt`.
