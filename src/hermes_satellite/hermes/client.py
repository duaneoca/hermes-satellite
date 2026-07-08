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
            # Hermes rejects control chars and keys > 256 chars; fail here
            # with a clear message rather than as an opaque server error.
            if len(key) > 256 or any(c in key for c in "\r\n\x00"):
                raise HermesError(
                    "session key must be <=256 chars and contain no control "
                    f"characters (got {len(key)} chars)"
                )
            headers["X-Hermes-Session-Key"] = key
        return headers

    def _payload(self, text: str, stream: bool) -> dict:
        messages = []
        if self._cfg.system_prompt:
            # Ask for speakable prose (replies are read aloud by TTS).
            messages.append({"role": "system", "content": self._cfg.system_prompt})
        messages.append({"role": "user", "content": text})
        return {"model": self._cfg.model, "messages": messages, "stream": stream}

    def send(self, text: str, session_key: str) -> str:
        payload = self._payload(text, stream=False)
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

    def send_stream(self, text: str, session_key: str):
        """Yield reply text deltas as they arrive (SSE, ``stream: true``).

        The configured ``timeout`` applies per-read in streaming mode — i.e.
        it bounds the silence *between* chunks, not the total reply time,
        which is the right semantics for a long-thinking agent. Raises
        :class:`HermesError`; failures before the first delta are safe to
        retry non-streaming.
        """
        import json as _json

        try:
            resp = self._session.post(
                self._url,
                json=self._payload(text, stream=True),
                headers=self._headers(session_key),
                timeout=self._cfg.timeout,
                stream=True,
            )
        except requests.Timeout as exc:
            raise HermesError(
                f"Hermes stream timed out after {self._cfg.timeout}s") from exc
        except requests.RequestException as exc:
            raise HermesError(f"Hermes stream failed: {exc}") from exc
        if resp.status_code != 200:
            body = resp.text[:200]
            resp.close()
            raise HermesError(f"Hermes returned HTTP {resp.status_code}: {body}")

        def deltas():
            try:
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw or not raw.startswith("data:"):
                        continue
                    data = raw[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = _json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content")
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise HermesError(
                            f"Unexpected stream chunk: {data[:120]}") from exc
                    if content:
                        yield content
            except requests.RequestException as exc:
                raise HermesError(f"Hermes stream broke mid-reply: {exc}") from exc
            finally:
                resp.close()

        return deltas()
