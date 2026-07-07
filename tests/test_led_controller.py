from hermes_satellite.leds.base import LEDState
from hermes_satellite.leds.controller import AnimatedLEDController, _STATE_COLORS
from hermes_satellite.leds.mock_backend import MockLEDBackend


def _render(state, t=0.0):
    backend = MockLEDBackend(num_leds=3)
    ctrl = AnimatedLEDController(backend, brightness=8)
    return ctrl._render(state, t)


def test_off_is_all_black():
    assert _render(LEDState.OFF) == [(0, 0, 0)] * 3


def test_solid_states_use_base_color():
    for state in (LEDState.WAKE, LEDState.RECORDING, LEDState.MUTED):
        frame = _render(state)
        assert frame == [_STATE_COLORS[state]] * 3


def test_processing_spinner_has_one_bright_led():
    frame = _render(LEDState.PROCESSING, t=0.0)
    base = _STATE_COLORS[LEDState.PROCESSING]
    assert frame.count(base) == 1  # exactly one active LED
    # The other LEDs are dimmed, not off.
    dims = [c for c in frame if c != base]
    assert all(c != (0, 0, 0) for c in dims)


def test_idle_breathing_varies_with_time():
    a = _render(LEDState.IDLE, t=0.0)
    b = _render(LEDState.IDLE, t=1.0)
    assert a != b  # brightness scale changes over time


def test_brightness_forwarded_to_backend():
    backend = MockLEDBackend(num_leds=3)
    AnimatedLEDController(backend, brightness=20)
    assert backend._brightness == 20


def test_set_state_and_backend_frame_flow():
    backend = MockLEDBackend(num_leds=3)
    ctrl = AnimatedLEDController(backend, brightness=8)
    # Directly render and push a frame (avoids starting the thread).
    frame = ctrl._render(LEDState.RECORDING, 0.0)
    backend.set_frame(frame)
    assert backend.last_frame == [_STATE_COLORS[LEDState.RECORDING]] * 3
