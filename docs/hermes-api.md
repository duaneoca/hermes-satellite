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

The session key lets each satellite device have its own **long‑term memory
scope** on the Hermes side. `HermesClient.send(text, session_key)` uses the
per‑call `session_key`, falling back to `hermes.session_key` from config when
empty.

> **Verified** against the [Hermes API Server
> docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
> (2026‑07‑08): `X-Hermes-Session-Key` is "a stable per‑channel identifier for
> long‑term memory," threaded to `AIAgent(gateway_session_key=...)`; the Honcho
> memory provider derives a stable scope from it. It is intentionally distinct
> from the transcript‑scoped `X-Hermes-Session-Id` (which rotates on `/new`),
> so a device keeps its memory across conversation resets. Give each satellite
> a distinct key (the hostname is a good default) for per‑device memory;
> reuse one key across devices to share memory.
>
> **Constraints:** max 256 characters; control characters (`\r`, `\n`, `\x00`)
> are rejected by the server. The client validates this before sending so a
> bad key fails clearly rather than as an opaque HTTP error.

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

## Streaming

`send_stream()` posts with `stream: true` and yields reply text deltas from
the Server-Sent Events response (`data:` lines, `[DONE]` terminator). The
pipeline chunks deltas into sentences (`iter_sentences`) and synthesizes
ahead while the previous sentence plays, so first audio arrives within
seconds of the reply starting. Enabled by default (`hermes.stream: true`).

Timeouts: `hermes.timeout` bounds the TCP connect (and the whole
non-streaming request); `hermes.stream_read_timeout` (default 300 s) bounds
the quiet gap between stream reads — deliberately long, because the agent
goes silent while it runs tools mid-turn.

## Duplicate-turn safety

The client never re-sends a message Hermes may already have. The streaming
path falls back to a non-streaming request **only** on
`HermesStreamNotStarted` — a connection failure or an immediate non-200
rejection, i.e. no turn started. A read timeout or a broken stream raises
`HermesError` instead of retrying. Rationale (field incident): a
quiet-stream timeout used to trigger the fallback, which re-POSTed the same
message mid-turn; the server's `busy_input_mode: interrupt` treated the
duplicate as new input and killed the in-flight turn, cascading into
retries, security-gate blocks, and inconsistent tool results.
