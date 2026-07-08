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
