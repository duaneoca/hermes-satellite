"""APA102 SPI LED driver.

Vendored for hermes-satellite from Seeed's ReSpeaker ``mic_hat`` project
(https://github.com/respeaker/mic_hat), which itself derives from Martin
Erzberger's APA102_Pi driver (https://github.com/tinue/APA102_Pi). MIT licensed.

The ReSpeaker 2-Mic Pi HAT (v1 and v2) drives 3 APA102 RGB LEDs over SPI. Each
LED frame is a brightness byte (0b111xxxxx) followed by three colour bytes; the
strip is framed by a 32-bit start frame of zeros and an end frame long enough to
clock the data through all LEDs.
"""

import spidev

RGB_MAP = {
    "rgb": [3, 2, 1],
    "rbg": [3, 1, 2],
    "grb": [2, 3, 1],
    "gbr": [2, 1, 3],
    "brg": [1, 3, 2],
    "bgr": [1, 2, 3],
}


class APA102:
    """Driver for APA102 LEDs (a.k.a. DotStar)."""

    # LED frame prefix: 3 header bits (111) + 5 brightness bits.
    MAX_BRIGHTNESS = 0b11111  # 31
    LED_START = 0b11100000    # header for the brightness byte

    def __init__(
        self,
        num_led,
        global_brightness=MAX_BRIGHTNESS,
        order="rgb",
        bus=0,
        device=1,
        max_speed_hz=8000000,
    ):
        self.num_led = num_led
        order = order.lower()
        self.rgb = RGB_MAP.get(order, RGB_MAP["rgb"])
        # Limit the global brightness to the valid 5-bit range.
        self.global_brightness = min(int(global_brightness), self.MAX_BRIGHTNESS)

        self.leds = [self.LED_START, 0, 0, 0] * self.num_led  # brightness,B,G,R per LED
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        if max_speed_hz:
            self.spi.max_speed_hz = max_speed_hz

    def clock_start_frame(self):
        self.spi.xfer2([0] * 4)  # 32 zero bits

    def clock_end_frame(self):
        # One extra clock cycle per two LEDs; xfer2 sends 8 bits per byte, so
        # (num_led + 15) // 16 bytes are enough to clock the last LED's data out.
        self.spi.xfer2([0xFF] * ((self.num_led + 15) // 16))

    def clear_strip(self):
        """Turn all LEDs off and immediately show it."""
        for led in range(self.num_led):
            self.set_pixel(led, 0, 0, 0)
        self.show()

    def set_pixel(self, led_num, red, green, blue, bright_percent=100):
        """Set the colour of one LED in the internal buffer (call show() to apply)."""
        if led_num < 0 or led_num >= self.num_led:
            return
        brightness = int(round(self.global_brightness * bright_percent / 100.0))
        ledstart = (brightness & 0b11111) | self.LED_START

        start_index = 4 * led_num
        self.leds[start_index] = ledstart
        self.leds[start_index + self.rgb[0]] = int(red) & 0xFF
        self.leds[start_index + self.rgb[1]] = int(green) & 0xFF
        self.leds[start_index + self.rgb[2]] = int(blue) & 0xFF

    def set_pixel_rgb(self, led_num, rgb_color, bright_percent=100):
        """Set a pixel from a packed 0xRRGGBB integer."""
        self.set_pixel(
            led_num,
            (rgb_color & 0xFF0000) >> 16,
            (rgb_color & 0x00FF00) >> 8,
            rgb_color & 0x0000FF,
            bright_percent,
        )

    def rotate(self, positions=1):
        """Rotate the LED buffer by the given number of positions."""
        cutoff = 4 * (positions % self.num_led)
        self.leds = self.leds[cutoff:] + self.leds[:cutoff]

    def show(self):
        """Transmit the buffer to the LED strip."""
        self.clock_start_frame()
        # xfer2 mutates its argument on some platforms; send a copy.
        self.spi.xfer2(list(self.leds))
        self.clock_end_frame()

    def cleanup(self):
        """Release the SPI device."""
        self.spi.close()
