"""Events and the state machine transition table.

The state machine is deliberately tiny and free of any component coupling: it
owns the current :class:`State`, an explicit transition table, and an observer
callback list. The LED controller subscribes to transitions rather than the
core knowing anything about LEDs.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Callable, Dict, List, Tuple

from .states import State

logger = logging.getLogger(__name__)

# Observer signature: (old_state, new_state) -> None
TransitionObserver = Callable[[State, State], None]


class Event(Enum):
    """Events that drive state transitions."""

    WAKE_DETECTED = "wake_detected"
    RECORDING_STARTED = "recording_started"
    SPEECH_CAPTURED = "speech_captured"
    RESPONSE_READY = "response_ready"
    PLAYBACK_DONE = "playback_done"
    ERROR = "error"
    RESET = "reset"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Explicit (state, event) -> state transition table for the happy path. Any
# (state, ERROR) pair routes to ERROR and RESET from any state returns to IDLE;
# those blanket rules are applied in dispatch() so they are not listed here. The
# RESET path also serves the "woke but no speech captured" abort back to IDLE.
_TRANSITIONS: Dict[Tuple[State, Event], State] = {
    (State.IDLE, Event.WAKE_DETECTED): State.WAKE,
    (State.WAKE, Event.RECORDING_STARTED): State.RECORD,
    (State.RECORD, Event.SPEECH_CAPTURED): State.PROCESS,
    (State.PROCESS, Event.RESPONSE_READY): State.SPEAK,
    (State.SPEAK, Event.PLAYBACK_DONE): State.IDLE,
}


class InvalidTransition(Exception):
    """Raised when an event is not valid for the current state."""

    def __init__(self, state: State, event: Event):
        super().__init__(f"No transition from {state} on {event}")
        self.state = state
        self.event = event


class StateMachine:
    """Thread-safe finite state machine for the voice pipeline."""

    def __init__(self, initial: State = State.IDLE):
        self._state = initial
        self._lock = threading.RLock()
        self._observers: List[TransitionObserver] = []

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def subscribe(self, observer: TransitionObserver) -> None:
        """Register a callback invoked on every state change."""
        with self._lock:
            self._observers.append(observer)

    def dispatch(self, event: Event) -> State:
        """Apply ``event`` and return the resulting state.

        Raises :class:`InvalidTransition` if the event is not valid for the
        current state (except ERROR/RESET which are always valid).
        """
        with self._lock:
            old = self._state
            new = self._resolve(old, event)
            if new is None:
                raise InvalidTransition(old, event)
            if new != old:
                self._state = new
                logger.info("state: %s -> %s (%s)", old, new, event)
                observers = list(self._observers)
            else:
                observers = []
        # Notify outside the lock so observers can't deadlock the machine.
        for observer in observers:
            try:
                observer(old, new)
            except Exception:  # pragma: no cover - observer isolation
                logger.exception("state transition observer failed")
        return new

    @staticmethod
    def _resolve(state: State, event: Event):
        if event is Event.ERROR:
            return State.ERROR
        if event is Event.RESET:
            return State.IDLE
        return _TRANSITIONS.get((state, event))
