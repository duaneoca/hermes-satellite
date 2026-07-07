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

After reboot the card should appear in `arecord -l`. **The mainline wm8960
driver defaults most of the audio-path switches OFF** — capture records
silence and playback is inaudible until you wire the paths up. This recipe is
field-verified (Pi 4, kernel 6.18, ReSpeaker 2-Mic v1):

```bash
C="seeed2micvoicec"
# capture path: mic -> LINPUT1/RINPUT1 -> boost mixer -> PGA -> ADC
amixer -c $C sset 'Left Input Mixer Boost' on
amixer -c $C sset 'Right Input Mixer Boost' on
amixer -c $C sset 'Left Boost Mixer LINPUT1' on
amixer -c $C sset 'Right Boost Mixer RINPUT1' on
amixer -c $C sset 'Capture' 40 cap      # input PGA ~ +12.75 dB, capture enabled
amixer -c $C sset 'ADC PCM' 195         # digital capture volume, 195 = 0 dB
# playback path: DAC -> output mixer -> speaker/headphone
amixer -c $C sset 'Left Output Mixer PCM' on
amixer -c $C sset 'Right Output Mixer PCM' on
amixer -c $C sset 'Playback' 255        # DAC digital volume
amixer -c $C sset 'Speaker' 121         # JST speaker; 121 = 0 dB (the scale is
amixer -c $C sset 'Headphone' 110       # very logarithmic: 70% is -32 dB!)
amixer -c $C sset 'Speaker DC' 5        # class-D boost 0-5; low values = quiet
amixer -c $C sset 'Speaker AC' 5
```

Loop-test and **check the capture level** — clipping silently ruins wake-word
and STT accuracy:

```bash
arecord -D plughw:$C -f S16_LE -r 16000 -c 2 test.wav   # talk from across the
aplay  -D plughw:$C test.wav                            # room, then Ctrl-C
python3 - <<'EOF'
import wave, array
w = wave.open("test.wav")
rate = w.getframerate()
data = array.array('h', w.readframes(w.getnframes()))
for name, ch in (("LEFT", data[0::2]), ("RIGHT", data[1::2])):
    a = sorted(abs(s) for s in ch)
    n = len(a)
    imax = max(range(len(ch)), key=lambda i: abs(ch[i]))
    print(f"{name}: p99 {100*a[int(n*0.99)]/32767:.0f}%  "
          f"peak {100*a[-1]/32767:.0f}% (at t={imax/rate:.2f}s)  "
          f"samples>95%: {sum(1 for s in a if s > 31000)}")
EOF
```

**Read the p99 number, not the peak.** Opening the capture device produces a
~15 ms full-scale pop at t=0.00 on this codec, which pins any peak-only
measurement at 100% regardless of your gain settings (it cost this guide's
authors an hour). The pop is harmless in practice — the daemon opens the mic
once at startup and holds it. A healthy take shows the max at t≈0.00, a small
`samples>95%` count, and matched L/R p99 values.

Speech from your normal talking distance should land **p99 ≈ 30-70%**.
Field-verified endpoint on a 2-Mic v1 at ~2 m: `Capture` 63, `ADC PCM` 220 →
p99 ≈ 34%. Calibration rules learned the hard way:

- **Keep takes comparable**: same phrase, same distance, same voice level —
  position dominates everything (close-talking vs across the room is 20+ dB,
  more than any mixer setting).
- **Scales**: `Capture` (analog PGA) is 0-63, 63 = +30 dB — amixer silently
  clamps larger values. `ADC PCM` (digital) is 0-255, 195 = 0 dB, 0.5 dB/step.
- **At conversational distance these mics need lots of gain**: expect to run
  `Capture` at or near 63 and then tune with `ADC PCM` (start ~220 = +12.5 dB;
  ±10 steps = ±5 dB) until speech lands p99 ≈ 30-70%. Close-mic use is the
  opposite: back `Capture` off first.
- **If raising gain doesn't raise the peak**, check the automatic level
  control isn't overriding the PGA: `amixer -c $C sget 'ALC Function'` must be
  `None` (`amixer -c $C sset 'ALC Function' None`).

When it lands in range, persist everything: `sudo alsactl store`.

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

> Branch availability lags new kernels significantly: at the time of writing,
> Raspberry Pi OS Trixie ships kernel **6.18** (`6.18.34+rpt-rpi-v8`) while the
> newest branch was **v6.14** (check with
> `git ls-remote --heads https://github.com/HinTak/seeed-voicecard`). If your
> kernel is newer than every branch, use Option A. After an `apt full-upgrade` that bumps the
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
