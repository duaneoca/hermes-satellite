"""Hermes agent backend client."""

from .base import AgentClient, HermesError
from .client import HermesClient

__all__ = ["AgentClient", "HermesClient", "HermesError", "build_hermes"]


def build_hermes(config, demo: bool = False) -> AgentClient:
    """Construct the agent client. Demo mode uses a canned mock reply."""
    if demo:
        from .mock_client import MockAgentClient

        return MockAgentClient()
    return HermesClient(config.hermes)
