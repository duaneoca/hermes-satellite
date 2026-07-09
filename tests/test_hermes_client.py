import pytest
import responses

from hermes_satellite.config import HermesConfig
from hermes_satellite.hermes.base import HermesError
from hermes_satellite.hermes.client import HermesClient

URL = "http://127.0.0.1:8642/v1/chat/completions"


def _config(**kw):
    defaults = dict(
        host="127.0.0.1", port=8642, api_key="secret", session_key="cfg-session",
        model="hermes", timeout=5.0,
    )
    defaults.update(kw)
    return HermesConfig(**defaults)


@responses.activate
def test_send_sets_headers_and_payload_and_parses_reply():
    responses.add(
        responses.POST, URL,
        json={"choices": [{"message": {"role": "assistant", "content": "hi there"}}]},
        status=200,
    )
    client = HermesClient(_config())
    reply = client.send("hello", session_key="dev-42")
    assert reply == "hi there"

    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Bearer secret"
    # Per-call session key overrides the configured default.
    assert req.headers["X-Hermes-Session-Key"] == "dev-42"
    import json
    body = json.loads(req.body)
    assert body["model"] == "hermes"
    assert body["stream"] is False
    # Default config carries the speakable-output system prompt.
    assert body["messages"][0]["role"] == "system"
    assert "spoken aloud" in body["messages"][0]["content"]
    assert body["messages"][-1] == {"role": "user", "content": "hello"}


@responses.activate
def test_empty_system_prompt_sends_user_message_only():
    responses.add(
        responses.POST, URL,
        json={"choices": [{"message": {"content": "ok"}}]}, status=200,
    )
    HermesClient(_config(system_prompt="")).send("hi", session_key="d")
    import json
    body = json.loads(responses.calls[0].request.body)
    assert body["messages"] == [{"role": "user", "content": "hi"}]


@responses.activate
def test_falls_back_to_configured_session_key():
    responses.add(
        responses.POST, URL,
        json={"choices": [{"message": {"content": "ok"}}]}, status=200,
    )
    HermesClient(_config()).send("hi", session_key="")
    assert responses.calls[0].request.headers["X-Hermes-Session-Key"] == "cfg-session"


@responses.activate
def test_http_error_raises_hermes_error():
    responses.add(responses.POST, URL, json={"error": "nope"}, status=401)
    with pytest.raises(HermesError):
        HermesClient(_config()).send("hi", session_key="x")


@responses.activate
def test_malformed_response_raises_hermes_error():
    responses.add(responses.POST, URL, json={"unexpected": True}, status=200)
    with pytest.raises(HermesError):
        HermesClient(_config()).send("hi", session_key="x")


@responses.activate
def test_non_json_response_raises_hermes_error():
    responses.add(responses.POST, URL, body="not json", status=200)
    with pytest.raises(HermesError):
        HermesClient(_config()).send("hi", session_key="x")


def test_no_api_key_omits_authorization_header():
    @responses.activate
    def run():
        responses.add(
            responses.POST, URL,
            json={"choices": [{"message": {"content": "ok"}}]}, status=200,
        )
        HermesClient(_config(api_key="")).send("hi", session_key="x")
        assert "Authorization" not in responses.calls[0].request.headers

    run()


def test_session_key_too_long_or_control_chars_rejected():
    client = HermesClient(_config())
    import pytest as _pytest
    with _pytest.raises(HermesError, match="session key"):
        client.send("hi", session_key="x" * 257)
    with _pytest.raises(HermesError, match="control"):
        client.send("hi", session_key="bad\nkey")


def _sse_body(*chunks, done=True):
    lines = []
    for c in chunks:
        import json as _j
        lines.append("data: " + _j.dumps(
            {"choices": [{"delta": {"content": c}}]}))
        lines.append("")
    if done:
        lines.append("data: [DONE]")
    return "\n".join(lines)


@responses.activate
def test_send_stream_yields_deltas_and_sets_stream_true():
    responses.add(responses.POST, URL, body=_sse_body("Hel", "lo ", "there."),
                  status=200, content_type="text/event-stream")
    client = HermesClient(_config())
    out = list(client.send_stream("hi", session_key="d"))
    assert out == ["Hel", "lo ", "there."]
    import json
    body = json.loads(responses.calls[0].request.body)
    assert body["stream"] is True
    assert responses.calls[0].request.headers["X-Hermes-Session-Key"] == "d"


@responses.activate
def test_send_stream_http_error_raises_before_iteration():
    responses.add(responses.POST, URL, body="nope", status=401)
    client = HermesClient(_config())
    with pytest.raises(HermesError, match="401"):
        client.send_stream("hi", session_key="d")


@responses.activate
def test_send_stream_skips_agent_activity_events():
    """Field incident: Hermes interleaves tool-activity events (no 'choices')
    with completion chunks — every tool-using turn died on the first one."""
    import json as _j
    body = "\n".join([
        'data: ' + _j.dumps({"choices": [{"delta": {"content": "Checking. "}}]}),
        "",
        'data: ' + _j.dumps({"tool": "browser_navigate", "emoji": "x",
                             "label": "https://wttr.in/Oakland,CA"}),
        "",
        "data: {not json}",   # unparseable line: skipped too, not fatal
        "",
        'data: ' + _j.dumps({"choices": [{"delta": {"content": "It is 71F."}}]}),
        "",
        "data: [DONE]",
    ])
    responses.add(responses.POST, URL, body=body,
                  status=200, content_type="text/event-stream")
    client = HermesClient(_config())
    assert list(client.send_stream("hi", session_key="d")) == \
        ["Checking. ", "It is 71F."]


@responses.activate
def test_send_stream_only_activity_events_yields_nothing():
    import json as _j
    body = "\n".join([
        'data: ' + _j.dumps({"tool": "terminal", "label": "curl"}),
        "",
        "data: [DONE]",
    ])
    responses.add(responses.POST, URL, body=body,
                  status=200, content_type="text/event-stream")
    client = HermesClient(_config())
    assert list(client.send_stream("hi", session_key="d")) == []


def test_stream_connection_error_is_retryable(monkeypatch):
    """Connection failures never started a turn -> HermesStreamNotStarted."""
    import requests
    from hermes_satellite.config import HermesConfig
    from hermes_satellite.hermes.base import HermesStreamNotStarted
    from hermes_satellite.hermes.client import HermesClient

    class BoomSession:
        def post(self, *a, **k):
            raise requests.ConnectionError("refused")

    client = HermesClient(HermesConfig(), session=BoomSession())
    with pytest.raises(HermesStreamNotStarted):
        client.send_stream("hi", "key")


def test_stream_rejection_is_retryable():
    """A non-200 rejected the request outright -> safe to retry blocking."""
    from hermes_satellite.config import HermesConfig
    from hermes_satellite.hermes.base import HermesStreamNotStarted
    from hermes_satellite.hermes.client import HermesClient

    class Resp:
        status_code = 400
        text = "streaming unsupported"

        def close(self):
            pass

    class Session:
        def post(self, *a, **k):
            return Resp()

    client = HermesClient(HermesConfig(), session=Session())
    with pytest.raises(HermesStreamNotStarted):
        client.send_stream("hi", "key")


def test_stream_read_timeout_is_not_retryable():
    """Regression (field incident): a read timeout means Hermes already has
    the message — must NOT be the retryable class, or the caller re-sends
    and creates a duplicate turn."""
    import requests
    from hermes_satellite.config import HermesConfig
    from hermes_satellite.hermes.base import HermesError, HermesStreamNotStarted
    from hermes_satellite.hermes.client import HermesClient

    class Session:
        def post(self, *a, **k):
            raise requests.ReadTimeout("quiet")

    client = HermesClient(HermesConfig(), session=Session())
    with pytest.raises(HermesError) as e:
        client.send_stream("hi", "key")
    assert not isinstance(e.value, HermesStreamNotStarted)


def test_stream_uses_patient_read_timeout():
    """Streaming requests pass (connect, stream_read_timeout) — agent tool
    phases are quiet and must not be cut off at the blocking timeout."""
    from hermes_satellite.config import HermesConfig
    from hermes_satellite.hermes.client import HermesClient

    seen = {}

    class Resp:
        status_code = 200

        def iter_lines(self, decode_unicode=True):
            yield 'data: {"choices":[{"delta":{"content":"hi"}}]}'
            yield "data: [DONE]"

        def close(self):
            pass

    class Session:
        def post(self, *a, **k):
            seen["timeout"] = k["timeout"]
            return Resp()

    cfg = HermesConfig(timeout=30.0, stream_read_timeout=300.0)
    client = HermesClient(cfg, session=Session())
    assert list(client.send_stream("hi", "key")) == ["hi"]
    assert seen["timeout"] == (30.0, 300.0)
