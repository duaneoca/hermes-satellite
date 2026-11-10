# Raspberry Pi Zero 2 W + ReSpeaker 2-Mic HAT v1

Profile: `hardware_profile: pizero2-respeaker-v1`

| Aspect        | Value                                                    |
| ------------- | -------------------------------------------------------- |
| Audio codec   | WM8960 (kernel `wm8960-soundcard` overlay)               |
| LEDs          | 3× APA102 on SPI0, CE1 → `/dev/spidev0.1`                |
| Button        | GPIO17 (BCM), active‑low pull‑up, via `lgpio`            |
| GPIO backend  | `lgpio`                                                  |
| RAM           | **512 MB**: needs swap and the small models (see §2)     |
| OS            | **64-bit** Raspberry Pi OS **Lite** (required)           |

The HAT side is **identical to the Pi 4**: same BCM GPIO numbering, same SPI
bus, same WM8960 overlay and mixer recipe. This guide covers only what's
different on the Zero 2 W, and points you to the
[Pi 4 guide](pi4-respeaker-v1.md) for the shared HAT steps.

What's actually different is resources: the Zero 2 W has a quarter of the
Pi 4's RAM and a slower CPU (4× Cortex-A53 @ 1 GHz). The default models
(Moonshine `base`, a `-medium` Piper voice) won't fit in 512 MB alongside
openWakeWord. On this board you need swap, `moonshine/tiny` and a `-low`
voice.

## 0. Before you start

- **Header:** the plain "Zero 2 W" ships without GPIO pins. Solder a 2×20
  header, or buy the "Zero 2 WH". The HAT won't make contact otherwise.
- **OS: 64-bit Raspberry Pi OS Lite** (Bookworm or Trixie). The 32-bit
  (armhf) image **won't work**: `onnxruntime` and `moonshine-voice` publish no
  armv7 wheels, so `pip install` fails. Check with `uname -m` → `aarch64`.
  Use Lite (no desktop) because a desktop alone uses most of the RAM.
- **Power:** use a solid 5 V / 2.5 A supply. Under-voltage, shown as
  `vcgencmd get_throttled` ≠ `0x0`, causes audio dropouts and random resets
  during inference spikes.

## 1. HAT setup: same as the Pi 4

Follow these sections of the [Pi 4 guide](pi4-respeaker-v1.md) unchanged:

1. **[Audio codec (WM8960)](pi4-respeaker-v1.md#1-audio-codec-wm8960)**,
   Option A (the built-in overlay):
   ```bash
   echo "dtoverlay=wm8960-soundcard,alsaname=seeed2micvoicec" | sudo tee -a /boot/firmware/config.txt
   sudo reboot
   ```
   then the mixer recipe (`scripts/wm8960-mixer.sh`) and the capture-level
   calibration. The Zero 2 W has no 3.5 mm jack or analog audio, so the seeed
   card is usually the only ALSA card and `input_device`/`output_device` can
   stay `null`.
2. **[Enable SPI](pi4-respeaker-v1.md#2-enable-spi-for-the-leds)**: expect
   `/dev/spidev0.1`.
3. **[Button & group membership](pi4-respeaker-v1.md#3-button--group-membership)**:
   `gpio`, `spi`, `audio` groups.

## 2. Make room: swap and GPU memory

Do this **before** installing. pip unpacking the onnxruntime and piper
wheels can run out of memory on 512 MB with no swap.

```bash
free -m          # look at the "Swap:" total
```

You want **≥ 1 GB swap**. (`hermes-satellite doctor` flags anything less on
this board.)

- **Bookworm** (`dphys-swapfile`):
  ```bash
  sudo sed -i 's/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
  sudo systemctl restart dphys-swapfile
  free -m
  ```
- **Any release** (manual swapfile, if the above doesn't apply):
  ```bash
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  free -m
  ```

Optional, headless only: give the GPU the minimum so the CPU gets that RAM
back:

```bash
echo "gpu_mem=16" | sudo tee -a /boot/firmware/config.txt
sudo reboot
```

Swap keeps the daemon from being OOM-killed during the wizard or a model
load. It doesn't make anything fast. In steady state the working set should
fit in RAM, and that's what the model choices in §4 are for.

## 3. Install

Same as the Pi 4 guide's [Install & run](pi4-respeaker-v1.md#4-install--run),
including its **Python ≤ 3.11** note (Trixie → uv-managed 3.11). Only the
extra and the profile name differ:

```bash
sudo apt install -y libportaudio2
git clone https://github.com/duaneoca/hermes-satellite.git
cd hermes-satellite
python -m venv .venv && source .venv/bin/activate   # Bookworm; Trixie: uv 3.11 venv
pip install --upgrade pip setuptools wheel
pip install -e ".[pizero2]"        # same packages as [pi4]
cp config.example.yaml config.yaml
sed -i 's/^hardware_profile:.*/hardware_profile: pizero2-respeaker-v1/' config.yaml
hermes-satellite --demo --config config.yaml   # LED + button smoke test
```

Expect the install to take a while (tens of minutes, mostly unpacking
wheels on the SD card).

## 4. Low-resource config (important)

Edit `config.yaml` (or set the same values in the wizard):

```yaml
hardware_profile: pizero2-respeaker-v1

audio:
  input_channels: 2          # seeed card usually only opens in stereo

stt:
  model: moonshine/tiny      # base is too heavy for 512 MB / A53 cores
  streaming: false           # a streaming model can't keep up in real time
                             # here; the wizard warns if you try

tts:
  voice: en_US-lessac-low    # any "-low"/"-x_low" voice; "-medium" voices
                             # are ~2x the RAM and synthesize too slowly

wakeword:
  backend: openwakeword      # fine on the Zero 2 W
  vad_threshold: 0.0         # keep the extra Silero VAD model off
  noise_suppression: false

log_level: WARNING           # less SD-card writing
```

Expect turns to be noticeably slower than on a Pi 4: transcription takes
roughly 2–3× as long. The Hermes agent runs elsewhere, so that part is
unaffected. Tune `audio.silence_ms` in the wizard's Transcription section
to win some latency back.

Then run the wizard as usual:

```bash
hermes-satellite setup --config config.yaml
```

## 5. Running as a service

Follow [Running as a service](../hermes-satellite.md#running-as-a-service)
with two substitutions:

- install the `[pizero2]` extra:
  `sudo /opt/hermes-satellite/.venv/bin/pip install -e "/opt/hermes-satellite[pizero2]"`
- pre-seed the **tiny** Moonshine model (not BASE):
  ```bash
  sudo XDG_CACHE_HOME=/var/lib/hermes-satellite/cache /opt/hermes-satellite/.venv/bin/python -c "import moonshine_voice as mv; mv.get_model_for_language('en', mv.ModelArch.TINY)"
  ```

Uncomment the `ExecStartPre=…/wm8960-mixer.sh` line in the unit, the same as
on the Pi 4. `~/update-satellite` picks the `[pizero2]` extra automatically.

## Troubleshooting

- **Daemon dies silently / `Killed` / restarts in a loop** → OOM. Check
  `journalctl -k | grep -i oom` and `free -m`. Add swap (§2), and confirm
  `stt.model: moonshine/tiny` and a `-low` voice.
- **`No matching distribution found for onnxruntime`** (or moonshine) →
  you're on the 32-bit OS (`uname -m` says `armv7l`). Reflash with the
  64-bit Lite image.
- **Choppy playback / audio underruns** → CPU saturated or under-voltage.
  Check `vcgencmd get_throttled` (want `0x0`) and use a lower-quality voice.
- **Laggy replies over Wi-Fi** → Wi-Fi power saving. With NetworkManager
  (Bookworm+):
  `sudo nmcli connection modify "$(nmcli -g NAME connection show --active | head -1)" 802-11-wireless.powersave 2`,
  then reconnect.
- HAT-specific problems (deaf mic, missing card, LEDs) → the Pi 4 guide's
  [Troubleshooting](pi4-respeaker-v1.md#troubleshooting). It's the same
  hardware.
