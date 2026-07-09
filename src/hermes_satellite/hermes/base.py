"""Agent client abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod


class HermesError(Exception):
    """Raised when a request to the Hermes backend fails."""


class HermesStreamNotStarted(HermesError):
    """The streaming request never started a turn on Hermes (connection
    failure or the server rejected it) — the ONLY case where re-sending the
    same message non-streaming is safe. Anything later (read timeout, broken
    stream) means Hermes already has the message; re-sending would create a
    duplicate turn. Field incident: the duplicate hit the server's
    busy_input_mode:interrupt, which killed the in-flight turn."""


class AgentClient(ABC):
    """Sends user text to an agent backend and returns the reply text."""

    @abstractmethod
    def send(self, text: str, session_key: str) -> str:
        """Send ``text`` and return the assistant's reply.

        ``session_key`` scopes per-device memory on the backend.
        """
