# Seeed software notes

Which Seeed (and Seeed‑adjacent) software `hermes-satellite` depends on, and the
kernel caveats that motivated the two hardware profiles.

## APA102 LED driver (both HATs)

- **What:** `vendor/apa102.py`, vendored from Seeed's
  [`respeaker/mic_hat`](https://github.com/respeaker/mic_hat) (itself derived
  from [tinue/APA102_Pi](https://github.com/tinue/APA102_Pi), MIT).
- **Why vendored:** it is a single small, stable file with no packaging on PyPI;
  vendoring pins it and avoids a git dependency. Only `spidev` is required at
  runtime.
- **Same for v1 and v2:** both HAT revisions use 3× APA102 over SPI, so the LED
  code is identical across boards — only the SPI bus/device differ, and those are
  config‑driven.

## Audio codec drivers — the key v1 vs v2 difference

The two HATs use **different codecs**, which need different software:

| HAT | Codec         | Software                                             |
| --- | ------------- | ---------------------------------------------------- |
| v1  | WM8960        | `seeed-voicecard` DKMS out‑of‑tree driver            |
| v2  | TLV320AIC3104 | **device‑tree overlay** (no DKMS driver)             |

### v1 / WM8960 / `seeed-voicecard`

- Upstream: [`respeaker/seeed-voicecard`](https://github.com/respeaker/seeed-voicecard).
- **Caveat:** upstream lags current kernels. On recent Raspberry Pi OS
  (Bookworm, kernel 6.x) use the maintained
  [HinTak fork](https://github.com/HinTak/seeed-voicecard) (branch per kernel
  version). The in‑tree `wm8960` codec has bugs the Seeed build fixes, so the
  module is built via DKMS.
- If you cannot get DKMS to build, Seeed also publishes a pre‑configured SD image.

### v2 / TLV320AIC3104 / overlay

- Supported through a **device‑tree overlay**, per the current
  [Seeed 2‑Mic HAT v2 wiki](https://wiki.seeedstudio.com/respeaker_2_mics_pi_hat_raspberry_v2/).
  There is no DKMS kernel module to build.
- The overlay source/name changes between Seeed releases — always follow the
  wiki for your OS version rather than a pinned copy.

## Raspberry Pi 5 / RP1 caveats

The Pi 5 replaced the legacy GPIO/SPI blocks with the **RP1** chip:

- **`RPi.GPIO` is incompatible.** Use `lgpio` (or `gpiozero` on top of it). The
  `pi5-respeaker-v2` profile selects the `lgpio` button backend.
- **SPI enumeration differs.** The header SPI can appear as `/dev/spidev10.x`
  instead of `/dev/spidev0.x`. `leds.spi_bus`/`leds.spi_device` are configurable
  precisely so you can match whatever your kernel exposes.
- **gpiochip numbering.** The 40‑pin header is `gpiochip0` on current Bookworm,
  `gpiochip4` on some earlier kernels. `LgpioButton` tries both.

## Not used

- We do **not** use Seeed's `pixel_ring` package (that targets the 4/6‑mic
  circular arrays); the 2‑Mic HAT's 3 LEDs are driven directly via the APA102
  driver.
- We do **not** rely on the v2 HAT's onboard NLU/VAD/DOA/KWS firmware features —
  wake word (Porcupine), VAD (webrtcvad) and STT (Moonshine) run on the Pi so the
  pipeline is identical across both HATs.
