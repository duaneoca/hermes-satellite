# Hermes API integration

`hermes-satellite` talks to the Hermes agent backend over its
**OpenAI‑compatible** chat API. This guide documents the exact contract the
client (`src/hermes_satellite/hermes/client.py`) speaks.

> **Contract source:** this is implemented against the described contract
> (endpoint, auth header, session‑key header, OpenAI‑shaped body). If the real
> Hermes API differs, adjust `HermesClient` and this doc together — the client is
> the single integration point.

## Endpoint

```
POST http://<host>:<port>/v1/chat/completions
```

Defaults: `host: 127.0.0.1`, `port: 8642` (from `hermes:` in config).

## Authentication & session scoping

| Header                  | Value                         | Purpose                                  |
| ----------------------- | ----------------------------- | ---------------------------------------- |
| `Authorization`         | `Bearer <hermes.api_key>`     | API auth (omitted if no key configured)  |
| `X-Hermes-Session-Key`  | `<session_key>`               | **Per‑device memory scoping** on Hermes  |
| `Content-Type`          | `application/json`            |                                          |

The session key lets each satellite device have its own conversational memory on
the Hermes side. `HermesClient.send(text, session_key)` uses the per‑call
`session_key`, falling back to `hermes.session_key` from config when empty.

Provide secrets via env vars to keep them out of `config.yaml`:

```bash
export HERMES_API_KEY=sk-...
export HERMES_SESSION_KEY=kitchen-satellite
```

## Request body

OpenAI chat‑completions shape, non‑streaming:

```json
{
  "model": "hermes",
  "messages": [{ "role": "user", "content": "what time is it" }],
  "stream": false
}
```

`model` comes from `hermes.model`. Today a single user turn is sent per
utterance; conversational history is expected to live on the Hermes side, keyed
by the session key.

## Response

Standard OpenAI shape; the client reads `choices[0].message.content`:

```json
{
  "choices": [
    { "message": { "role": "assistant", "content": "It is 4 o'clock." } }
  ]
}
```

## Errors

`HermesClient` raises `HermesError` (a single, catchable type) for:

- connection failures / timeouts (`hermes.timeout`, default 30 s),
- non‑200 responses (status + truncated body included),
- non‑JSON bodies, missing `choices`, or null content.

The pipeline catches these, flashes the `ERROR` LED, and returns to `IDLE`.

## Verifying against a running Hermes

```bash
curl -sS http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer $HERMES_API_KEY" \
  -H "X-Hermes-Session-Key: test-device" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes","messages":[{"role":"user","content":"hello"}],"stream":false}'
```

You should get a JSON completion back. The unit tests in
`tests/test_hermes_client.py` assert the client sends exactly these headers and
body and parses the reply.

## Future: streaming

The client is structured so a streaming path (`stream: true`, Server‑Sent
Events, piping sentence chunks to Piper as they arrive) can be added without
changing callers. It is intentionally out of scope for this first
implementation.
