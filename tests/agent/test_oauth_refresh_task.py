"""Tests for the MCP OAuth token refresh background task in AgentLoop.

The refresh task only starts when an MCP server declares ``auth``, so a typo in
the scheduling call stays dormant until someone actually configures OAuth — and
then crashes the gateway at startup. These tests pin the wiring.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import MCPServerConfig, OAuthConfig, ToolsConfig


def _make_loop(tmp_path, *, mcp_servers: dict | None = None) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    # Post-#5343 the loop reads MCP servers from tools_config, not a mcp_servers param.
    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        tools_config=ToolsConfig(mcp_servers=mcp_servers or {}),
    )


@pytest.mark.asyncio
async def test_oauth_refresh_task_scheduled_for_server_with_auth(tmp_path):
    loop = _make_loop(
        tmp_path,
        mcp_servers={
            "remote": MCPServerConfig(url="https://example.test/mcp", auth=OAuthConfig()),
        },
    )

    loop._start_oauth_refresh_task()

    task = loop._oauth_refresh_task
    assert task is not None
    # The refresher runs for the loop's lifetime, so it must not count as
    # drained background work (that would block maintenance() forever).
    assert not loop._has_pending_work()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_oauth_refresh_task_skipped_without_auth(tmp_path):
    loop = _make_loop(
        tmp_path,
        mcp_servers={"remote": MCPServerConfig(url="https://example.test/mcp")},
    )

    loop._start_oauth_refresh_task()

    assert loop._oauth_refresh_task is None


@pytest.mark.asyncio
async def test_oauth_refresh_task_skipped_without_servers(tmp_path):
    loop = _make_loop(tmp_path)

    loop._start_oauth_refresh_task()

    assert loop._oauth_refresh_task is None
