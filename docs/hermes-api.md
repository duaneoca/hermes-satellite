# Hermes API integration

`hermes-satellite` talks to the Hermes agent backend over its
**OpenAI‑compatible** chat API. This guide documents the exact contract the
client (`src/hermes_satellite/hermes/client.py`) speaks.

> **Contract source:** verified against a live Hermes API server
> (2026‑07‑07): endpoint, bearer auth, OpenAI body/response shape and the
> `hermes-agent` model id all confirmed end‑to‑end from a satellite. If your
> Hermes differs, adjust `HermesClient` and this doc together — the client is
> the single integration point.

## Server-side setup (enabling network access on Hermes)

Verified against a live Hermes install (macOS host, 2026‑07‑07). The API
Server platform is **env-driven** — there is no config.yaml section for it on
the Hermes side; it auto-enables when `API_SERVER_KEY` exists in
`~/.hermes/.env`:

```
API_SERVER_KEY=<openssl rand -hex 32>
API_SERVER_HOST=0.0.0.0     # REQUIRED for satellites: default binds 127.0.0.1
# API_SERVER_PORT=8642      # default
```

then `hermes gateway restart` (env changes don't apply live). Gotchas:

- `API_SERVER_HOST=0.0.0.0` is the line people miss — without it the API is
  localhost-only and satellites see **connect timeouts**.
- The server refuses to start without `API_SERVER_KEY` (intentional).
- Hermes also listens on other ports (dashboard, backend RPC, webhook
  receivers). Only **8642** is the OpenAI-compatible API — a port that
  answers `/health` but 404s `/v1/*` is one of the others.
- **`GET /health` requires no auth** and returns 200 — the perfect
  reachability probe from a satellite:

  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" http://<host>:8642/health   # expect 200
  ```

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

> **Open contract question:** the Hermes server's own API docs don't mention
> `X-Hermes-Session-Key`. Unknown headers are harmless, but if your Hermes
> build doesn't implement session scoping, all satellites share one memory —
> verify by asking the agent something device-specific from two session keys.

Provide secrets via env vars to keep them out of `config.yaml`:

```bash
export HERMES_API_KEY=sk-...
export HERMES_SESSION_KEY=kitchen-satellite
```

## Request body

OpenAI chat‑completions shape, non‑streaming:

```json
{
  "model": "hermes-agent",
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
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"hello"}],"stream":false}'
```

You should get a JSON completion back. The unit tests in
`tests/test_hermes_client.py` assert the client sends exactly these headers and
body and parses the reply.

## Security posture (from the server's own docs)

- **The bearer key grants full agent access** — terminal and filesystem on
  the Hermes host. Treat it like a root password: on the satellite it belongs
  in `/etc/hermes-satellite/secrets.env` (root:root, 600), never in
  world-readable config.
- **The API is plain HTTP.** The key crosses the LAN unencrypted on every
  request — acceptable on a trusted home network path, but if the
  satellite→Hermes flow crosses segments you don't fully trust, front Hermes
  with a TLS reverse proxy (Caddy/nginx) and point `hermes.host`/`port` at it.
- Rotate by editing the server's `.env` + `hermes gateway restart`, then
  updating `secrets.env` on each satellite.

## Future: streaming

The client is structured so a streaming path (`stream: true`, Server‑Sent
Events, piping sentence chunks to Piper as they arrive) can be added without
changing callers. It is intentionally out of scope for this first
implementation.
