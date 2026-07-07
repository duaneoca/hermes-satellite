"""Mock agent client for demo/testing (no Hermes server needed)."""

from __future__ import annotations

import logging

from .base import AgentClient

logger = logging.getLogger(__name__)


class MockAgentClient(AgentClient):
    def __init__(self, reply: str = "It is half past demo o'clock."):
        self._reply = reply

    def send(self, text: str, session_key: str) -> str:
        logger.info("mock hermes: received %r (session=%s)", text, session_key or "-")
        return self._reply
