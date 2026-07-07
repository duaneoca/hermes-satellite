"""Pipeline states."""

from __future__ import annotations

from enum import Enum


class State(Enum):
    """The states of the voice pipeline.

    Happy path: IDLE -> WAKE -> RECORD -> PROCESS -> SPEAK -> IDLE.
    ERROR is reachable from any state and returns to IDLE via RESET.
    """

    IDLE = "idle"
    WAKE = "wake"
    RECORD = "record"
    PROCESS = "process"
    SPEAK = "speak"
    ERROR = "error"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value
