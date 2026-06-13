from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from nanobot_channel_mcpserver import MCPServerChannel, MCPServerConfig


class _DummyBus:
    async def publish_inbound(self, msg):
        return None


def _channel() -> MCPServerChannel:
    return MCPServerChannel(MCPServerConfig(enabled=True), _DummyBus())


def _request(host: str = "127.0.0.1", headers: dict[str, str] | None = None):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers or {},
        state=SimpleNamespace(),
    )


def test_auth_map_uses_explicit_key_mapping() -> None:
    channel = MCPServerChannel(
        MCPServerConfig(
            enabled=True,
            api_keys={"k-owner": "owner", "k-reader": "reader"},
        ),
        _DummyBus(),
    )

    assert channel._auth_map()["k-owner"] == "owner"
    assert channel._auth_map()["k-reader"] == "reader"


def test_authenticate_request_requires_valid_bearer_token() -> None:
    channel = MCPServerChannel(
        MCPServerConfig(enabled=True, api_keys={"k-owner": "owner"}),
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


def test_validate_origin_allows_missing_origin_and_exact_matches() -> None:
    channel = MCPServerChannel(
        MCPServerConfig(enabled=True, allowed_origins=["https://allowed.example"]),
        _DummyBus(),
    )

    channel._validate_origin(_request())
    channel._validate_origin(_request(headers={"origin": "https://allowed.example"}))

    with pytest.raises(HTTPException) as denied:
        channel._validate_origin(_request(headers={"origin": "https://denied.example"}))
    assert denied.value.status_code == 403


def test_allow_sender_id_includes_session_conversation_and_host() -> None:
    channel = _channel()
    req = _request(host="10.0.0.5")
    req.state.auth_principal = "owner"

    sender = channel._allow_sender_id(
        req,
        {
            "sessionId": "sess-1",
            "conversationId": "conv-1",
        },
    )

    assert sender == "owner|conv-1|sess-1|http:10.0.0.5|10.0.0.5"


@pytest.mark.asyncio
async def test_initialize_returns_session_headers() -> None:
    channel = _channel()
    req = _request(headers={"mcp-protocol-version": "2025-03-26"})
    req.state.auth_principal = "owner"

    result = await channel._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        },
        req,
    )

    assert result["jsonrpc"] == "2.0"
    assert result["id"] == 1
    assert result["result"]["serverInfo"]["name"] == "nanobot-mcpserver"
    assert "MCP-Session-Id" in result["_headers"]


@pytest.mark.asyncio
async def test_tools_list_exposes_agent_chat() -> None:
    channel = _channel()
    req = _request(headers={"mcp-session-id": "sess-1"})
    req.state.auth_principal = "owner"

    result = await channel._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
        req,
    )

    tools = result["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "agent_chat"
