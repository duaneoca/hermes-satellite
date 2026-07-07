"""Agent client abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod


class HermesError(Exception):
    """Raised when a request to the Hermes backend fails."""


class AgentClient(ABC):
    """Sends user text to an agent backend and returns the reply text."""

    @abstractmethod
    def send(self, text: str, session_key: str) -> str:
        """Send ``text`` and return the assistant's reply.

        ``session_key`` scopes per-device memory on the backend.
        """
