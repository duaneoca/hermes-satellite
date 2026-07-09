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
from .base import AgentClient, HermesError, HermesStreamNotStarted

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

        Timeouts: ``timeout`` bounds the TCP connect; ``stream_read_timeout``
        bounds the silence between reads — deliberately long, because an
        agent backend goes quiet while it runs tools, and a "timeout" here
        does NOT mean the turn failed.

        Raises :class:`HermesStreamNotStarted` only when the request provably
        never started a turn (connection failure, or the server rejected it
        with a non-200) — the caller may then safely re-send non-streaming.
        Every other failure raises :class:`HermesError`: Hermes already has
        the message, and re-sending it would create a duplicate turn (field
        incident: the duplicate tripped the server's busy_input_mode:
        interrupt, killing the in-flight — and about to succeed — turn).
        """
        import json as _json

        try:
            resp = self._session.post(
                self._url,
                json=self._payload(text, stream=True),
                headers=self._headers(session_key),
                timeout=(self._cfg.timeout, self._cfg.stream_read_timeout),
                stream=True,
            )
        except requests.ConnectTimeout as exc:
            raise HermesStreamNotStarted(
                f"could not connect to Hermes within {self._cfg.timeout}s"
            ) from exc
        except requests.Timeout as exc:  # request delivered; Hermes has it
            raise HermesError(
                "Hermes stream went quiet for "
                f"{self._cfg.stream_read_timeout}s") from exc
        except requests.ConnectionError as exc:
            raise HermesStreamNotStarted(
                f"Hermes connection failed: {exc}") from exc
        except requests.RequestException as exc:
            raise HermesError(f"Hermes stream failed: {exc}") from exc
        if resp.status_code != 200:
            body = resp.text[:200]
            resp.close()
            # Rejected outright (e.g. streaming unsupported): no turn started.
            raise HermesStreamNotStarted(
                f"Hermes returned HTTP {resp.status_code}: {body}")

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
                    except ValueError:
                        logger.warning(
                            "skipping unparseable stream line: %.120s", data)
                        continue
                    choices = (chunk.get("choices")
                               if isinstance(chunk, dict) else None)
                    if not choices:
                        # Hermes interleaves agent-activity events (tool
                        # calls, status) with the completion chunks — e.g.
                        # {"tool": "browser_navigate", ...}. Not reply text;
                        # skip. (Field incident: treating these as fatal made
                        # every tool-using turn abort mid-stream.)
                        logger.debug("stream event (skipped): %.120s", data)
                        continue
                    try:
                        content = (choices[0].get("delta") or {}).get("content")
                    except (AttributeError, IndexError, TypeError):
                        logger.warning(
                            "skipping malformed completion chunk: %.120s",
                            data)
                        continue
                    if content:
                        yield content
            except requests.RequestException as exc:
                raise HermesError(f"Hermes stream broke mid-reply: {exc}") from exc
            finally:
                resp.close()

        return deltas()
