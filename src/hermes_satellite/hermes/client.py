"""Hermes OpenAI-compatible chat client.

Posts to ``/v1/chat/completions`` on the Hermes backend with bearer auth and an
``X-Hermes-Session-Key`` header for per-device memory scoping. Non-streaming:
the full completion is requested and returned. The request body is OpenAI
chat-completions shaped so the same backend contract is reused; a streaming
(``stream=true``, SSE) path can be layered on later without changing callers.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from ..config import HermesConfig
from .base import AgentClient, HermesError

logger = logging.getLogger(__name__)


class HermesClient(AgentClient):
    def __init__(self, config: HermesConfig, session: Optional[requests.Session] = None):
        self._cfg = config
        self._session = session or requests.Session()
        self._url = f"http://{config.host}:{config.port}/v1/chat/completions"

    def _headers(self, session_key: str) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"
        # Per-device memory scoping. Fall back to the configured session key.
        key = session_key or self._cfg.session_key
        if key:
            headers["X-Hermes-Session-Key"] = key
        return headers

    def send(self, text: str, session_key: str) -> str:
        messages = []
        if self._cfg.system_prompt:
            # Ask for speakable prose (replies are read aloud by TTS).
            messages.append({"role": "system", "content": self._cfg.system_prompt})
        messages.append({"role": "user", "content": text})
        payload = {
            "model": self._cfg.model,
            "messages": messages,
            "stream": False,
        }
        try:
            resp = self._session.post(
                self._url,
                json=payload,
                headers=self._headers(session_key),
                timeout=self._cfg.timeout,
            )
        except requests.Timeout as exc:
            raise HermesError(f"Hermes request timed out after {self._cfg.timeout}s") from exc
        except requests.RequestException as exc:
            raise HermesError(f"Hermes request failed: {exc}") from exc

        if resp.status_code != 200:
            raise HermesError(
                f"Hermes returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return self._parse(resp)

    @staticmethod
    def _parse(resp: requests.Response) -> str:
        try:
            data = resp.json()
        except ValueError as exc:
            raise HermesError("Hermes returned non-JSON response") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise HermesError(f"Unexpected Hermes response shape: {data!r}") from exc
        if content is None:
            raise HermesError("Hermes response had null content")
        return str(content)
