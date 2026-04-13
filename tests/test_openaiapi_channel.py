from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from nanobot_channel_openaiapi import OpenAIAPIChannel, OpenAIAPIConfig


class _DummyBus:
    async def publish_inbound(self, msg):
        return None


def _channel() -> OpenAIAPIChannel:
    return OpenAIAPIChannel(OpenAIAPIConfig(enabled=True), _DummyBus())


def _request(host: str = "127.0.0.1", headers: dict[str, str] | None = None):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers or {},
        state=SimpleNamespace(),
    )


def test_normalize_messages_uses_last_user_as_current_prompt() -> None:
    channel = _channel()

    history, prompt = channel._normalize_messages_for_agent(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Continue from previous"},
        ]
    )

    assert prompt == "Continue from previous"
    assert history == [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]


def test_normalize_messages_maps_developer_to_system() -> None:
    channel = _channel()

    history, prompt = channel._normalize_messages_for_agent(
        [
            {"role": "developer", "content": "Keep answers short."},
            {"role": "user", "content": "Ping"},
        ]
    )

    assert prompt == "Ping"
    assert history == [{"role": "system", "content": "Keep answers short."}]


def test_chat_id_prefers_user_then_payload_then_header_then_client() -> None:
    channel = _channel()

    req = _request(host="10.0.0.5", headers={"x-conversation-id": "hdr-42"})
    assert channel._chat_id(req, {"user": "user-1"}) == "user-1"
    assert channel._chat_id(req, {"conversation_id": "conv-2"}) == "conv-2"
    assert channel._chat_id(req, {}) == "hdr-42"
    assert channel._chat_id(_request(host="10.0.0.5"), {}) == "http:10.0.0.5"


def test_auth_map_uses_explicit_key_mapping() -> None:
    channel = OpenAIAPIChannel(
        OpenAIAPIConfig(
            enabled=True,
            api_keys={"k-owner": "owner", "k-reader": "reader"},
        ),
        _DummyBus(),
    )

    assert channel._auth_map()["k-owner"] == "owner"
    assert channel._auth_map()["k-reader"] == "reader"


def test_authenticate_request_requires_valid_bearer_token() -> None:
    channel = OpenAIAPIChannel(
        OpenAIAPIConfig(enabled=True, api_keys={"k-owner": "owner"}),
        _DummyBus(),
    )

    with pytest.raises(HTTPException) as missing:
        channel._authenticate_request(_request())
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as invalid:
        channel._authenticate_request(_request(headers={"authorization": "Bearer nope"}))
    assert invalid.value.status_code == 401

    principal = channel._authenticate_request(_request(headers={"authorization": "Bearer k-owner"}))
    assert principal == "owner"


def test_sender_id_uses_authenticated_principal() -> None:
    channel = _channel()
    req = _request()
    req.state.auth_principal = "owner"

    assert channel._sender_id(req) == "owner"

    req.state.auth_principal = ""
    with pytest.raises(HTTPException) as unauth:
        channel._sender_id(req)
    assert unauth.value.status_code == 401


def test_allow_sender_id_includes_openai_user_and_host() -> None:
    channel = _channel()
    req = _request(host="10.0.0.5")
    req.state.auth_principal = "owner"

    sender = channel._allow_sender_id(req, {"user": "my-client"})

    assert sender == "owner|my-client|http:10.0.0.5|10.0.0.5"


def test_is_allowed_matches_openai_user_alias() -> None:
    channel = OpenAIAPIChannel(
        OpenAIAPIConfig(enabled=True, allow_from=["my-client"]),
        _DummyBus(),
    )

    assert channel.is_allowed("owner|my-client|http:10.0.0.5|10.0.0.5") is True


def test_is_allowed_matches_host_alias() -> None:
    channel = OpenAIAPIChannel(
        OpenAIAPIConfig(enabled=True, allow_from=["10.0.0.5"]),
        _DummyBus(),
    )

    assert channel.is_allowed("owner|http:10.0.0.5|10.0.0.5") is True
