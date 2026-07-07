import pytest

from hermes_satellite.core.events import Event, InvalidTransition, StateMachine
from hermes_satellite.core.states import State


def test_happy_path():
    sm = StateMachine()
    assert sm.state is State.IDLE
    assert sm.dispatch(Event.WAKE_DETECTED) is State.WAKE
    assert sm.dispatch(Event.RECORDING_STARTED) is State.RECORD
    assert sm.dispatch(Event.SPEECH_CAPTURED) is State.PROCESS
    assert sm.dispatch(Event.RESPONSE_READY) is State.SPEAK
    assert sm.dispatch(Event.PLAYBACK_DONE) is State.IDLE


def test_error_reachable_from_any_state():
    for start in (State.IDLE, State.WAKE, State.RECORD, State.PROCESS, State.SPEAK):
        sm = StateMachine(initial=start)
        assert sm.dispatch(Event.ERROR) is State.ERROR


def test_reset_returns_to_idle():
    sm = StateMachine(initial=State.ERROR)
    assert sm.dispatch(Event.RESET) is State.IDLE


def test_invalid_transition_raises():
    sm = StateMachine()
    with pytest.raises(InvalidTransition):
        sm.dispatch(Event.PLAYBACK_DONE)  # not valid from IDLE


def test_no_speech_abort_via_reset():
    sm = StateMachine()
    sm.dispatch(Event.WAKE_DETECTED)
    sm.dispatch(Event.RECORDING_STARTED)
    assert sm.state is State.RECORD
    assert sm.dispatch(Event.RESET) is State.IDLE


def test_observer_receives_transitions():
    sm = StateMachine()
    seen = []
    sm.subscribe(lambda old, new: seen.append((old, new)))
    sm.dispatch(Event.WAKE_DETECTED)
    assert seen == [(State.IDLE, State.WAKE)]


def test_observer_not_called_on_noop():
    # RESET from IDLE stays IDLE -> no transition, no observer call.
    sm = StateMachine(initial=State.IDLE)
    seen = []
    sm.subscribe(lambda old, new: seen.append((old, new)))
    sm.dispatch(Event.RESET)
    assert seen == []


def test_observer_exception_isolated():
    sm = StateMachine()
    sm.subscribe(lambda old, new: (_ for _ in ()).throw(RuntimeError("boom")))
    calls = []
    sm.subscribe(lambda old, new: calls.append(new))
    # Should not raise despite the first observer failing.
    sm.dispatch(Event.WAKE_DETECTED)
    assert calls == [State.WAKE]
