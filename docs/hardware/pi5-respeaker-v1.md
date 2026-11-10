# Raspberry Pi 5 + ReSpeaker 2-Mic HAT v1

Profile: `hardware_profile: pi5-respeaker-v1`

| Aspect        | Value                                                           |
| ------------- | --------------------------------------------------------------- |
| Audio codec   | WM8960 (kernel `wm8960-soundcard` overlay)                      |
| LEDs          | 3× APA102 on SPI0 CE1 → `/dev/spidev0.1` *or* `/dev/spidev10.1` |
| Button        | GPIO17 (BCM), active‑low pull‑up, via `lgpio` (RP1)             |
| GPIO backend  | `lgpio`                                                         |

This combination mixes two existing guides: the **HAT** (codec, mixer) is
the same as on the [Pi 4](pi4-respeaker-v1.md), and the **board** (RP1 GPIO,
SPI numbering) is the same as with the [v2 HAT on a Pi 5](pi5-respeaker-v2.md).
This guide links to the shared steps and covers only the parts specific to
this pairing.

It's also the guide for the most common reason to own this pairing:
**running the satellite on the same Pi 5 as Hermes** (see §5).

> **Not field-verified yet.** The `wm8960-soundcard` overlay is used on the
> Pi 4 and Zero 2 W in this project. Other WM8960 boards (e.g. Waveshare)
> are commonly run on the Pi 5, but this exact pairing hasn't been validated
> here yet. If §1's card check fails, see Troubleshooting below, and please
> report what you find.

## 0. Before you start

- **Cooling:** the HAT covers the 40-pin header. The official Active Cooler
  usually leaves enough room, but check the HAT seats fully. If it doesn't,
  fit a 2×20 stacking header. A Pi 5 running Hermes plus the satellite
  without cooling will throttle.
- **Free pins/buses:** nothing else on the Pi may use I2S audio, SPI0 CE1
  or GPIO17. Check `/boot/firmware/config.txt` for other audio or SPI
  overlays (e.g. a DAC HAT you used before).

## 1. Audio codec (WM8960): same as the Pi 4

Follow the Pi 4 guide's
**[Audio codec (WM8960)](pi4-respeaker-v1.md#1-audio-codec-wm8960)**,
Option A, unchanged:

```bash
echo "dtoverlay=wm8960-soundcard,alsaname=seeed2micvoicec" | sudo tee -a /boot/firmware/config.txt
sudo reboot
arecord -l | grep -i seeed     # the card must appear here before you go on
```

Then apply the mixer recipe (`scripts/wm8960-mixer.sh`), do the capture-level
calibration, and run `sudo alsactl store`.

**Make the HAT the default card.** The Pi 5 has HDMI audio cards (no 3.5 mm
jack), and PortAudio may pick one of those. Create `/etc/asound.conf`:

```
defaults.pcm.card seeed2micvoicec
defaults.ctl.card seeed2micvoicec
```

If other software on this Pi needs a different default card (e.g. Hermes
plays audio over HDMI), skip `asound.conf` and pin the satellite's devices
with `audio.input_device` / `audio.output_device` instead. See
[Audio device config](pi4-respeaker-v1.md#audio-device-config-needed-or-not).

## 2. SPI and button: same as the Pi 5 v2 guide

Follow the Pi 5 v2 guide's
**[Enable SPI](pi5-respeaker-v2.md#2-enable-spi-and-confirm-the-led-device-node)**
and **[Button](pi5-respeaker-v2.md#3-button-lgpio-on-rp1)** sections:

- `ls -l /dev/spidev*`: if you see `/dev/spidev10.1` instead of
  `/dev/spidev0.1`, set `leds.spi_bus: 10`.
- The button runs on `lgpio` (the profile selects it). `RPi.GPIO` doesn't work on
  the RP1.
- Group membership: `gpio`, `spi`, `audio`.

## 3. Install

Same as the [Pi 5 v2 guide's Install & run](pi5-respeaker-v2.md#4-install--run),
including its **Python ≤ 3.11** note (Trixie → uv-managed 3.11 venv). Only
the profile name differs:

```bash
sudo apt install -y libportaudio2
git clone https://github.com/duaneoca/hermes-satellite.git
cd hermes-satellite
python -m venv .venv && source .venv/bin/activate   # Bookworm; Trixie: uv 3.11 venv
pip install --upgrade pip setuptools wheel
pip install -e ".[pi5]"            # same packages for both HAT revisions
cp config.example.yaml config.yaml
sed -i 's/^hardware_profile:.*/hardware_profile: pi5-respeaker-v1/' config.yaml
hermes-satellite --demo --config config.yaml   # LED + button smoke test
```

If mono capture fails, set `audio.input_channels: 2` (the seeed card usually
only opens in stereo). Then run the wizard:

```bash
hermes-satellite setup --config config.yaml
```

## 4. Running as a service

Follow [Running as a service](../hermes-satellite.md#running-as-a-service)
with the `[pi5]` extra, and **uncomment the `ExecStartPre=…/wm8960-mixer.sh`
line** in the unit, the same as on the Pi 4. `~/update-satellite` picks
`[pi5]` automatically.

## 5. Co-located with Hermes on the same Pi

You don't need a second Pi. Running the satellite on the Hermes host is
simpler than a separate device:

- **Connection:** keep the defaults, `hermes.host: 127.0.0.1` and
  `hermes.port: 8642`. You **don't** need `API_SERVER_HOST=0.0.0.0` on the
  Hermes side, and you can leave Hermes bound to localhost unless other
  satellites connect to it. You still need `API_SERVER_KEY` set in Hermes
  and the same key in `hermes.api_key` (or in `secrets.env` for the service).
  Probe it with `curl -s http://127.0.0.1:8642/health` (expect 200).
- **No network config:** the networking/VLAN guide doesn't apply, because
  no traffic leaves the box.
- **Isolation:** the satellite runs from its own venv
  (`/opt/hermes-satellite/.venv`, Python 3.11) as its own `hermes-sat` user,
  so it doesn't touch Hermes's Python environment or files.
- **Memory scope:** set `hermes.session_key` to a room name (e.g.
  `livingroom`) so the satellite gets its own memory scope on Hermes. If you
  leave it empty, no session header is sent. The Home Assistant device name
  still defaults to the hostname, which is now the Hermes box's name.
- **CPU:** the satellite's steady load is the always-on wake-word listener
  (a fraction of one core). Moonshine and Piper only run in short bursts
  per turn. If Hermes uses a remote LLM, there's plenty of headroom. If
  you also run a local LLM on this Pi, the two compete during a turn: expect
  slower transcription/speech, and avoid `stt.streaming` if the wizard
  warns that it can't keep up.
- **Startup order:** none needed. The satellite reaches Hermes per request,
  so it's fine if Hermes starts later. A turn made before Hermes is up just
  fails with the error tone.

## Troubleshooting

- **No `seeed2micvoicec` card after reboot** → check that the overlay
  loaded: `dmesg | grep -i wm8960` and `sudo vclog -m | grep -i overlay`.
  Make sure no other `dtoverlay=` line claims I2S, and that `dtparam=i2s=on`
  isn't commented out. If the overlay won't bind on your kernel, try the
  Pi 4 guide's Option B (HinTak `seeed-voicecard`, kernel-matched branch).
- **LEDs don't light** → wrong SPI node: try `leds.spi_bus: 10`.
- **`doctor` says board/profile mismatch** → on a Pi 5 it accepts
  `pi5-respeaker-v1` or `pi5-respeaker-v2`. Use v1 for this HAT.
- Deaf mic, clipping, and mixer problems → the Pi 4 guide's
  [Troubleshooting](pi4-respeaker-v1.md#troubleshooting). It's the same
  codec.
