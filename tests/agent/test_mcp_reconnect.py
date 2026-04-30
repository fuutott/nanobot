"""Tests for `MCPConnection` reconnect-on-transient-error behaviour.

These cover the contract between the wrapper retry loops and `MCPConnection`:
- On `ClosedResourceError`, the wrapper invokes `reconnect_if_stale`.
- After a successful reconnect, the retry runs against the fresh session.
- Concurrent callers don't all rebuild — `epoch` gates the actual rebuild.
- Reconnect failures surface as a distinct error message (not a silent loop).

Connection-internal `_open` / `_close` / transport-build logic is exercised by
`tests/tools/test_mcp_tool.py::test_connect_mcp_servers_*`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp import types as mcp_types

from nanobot.agent.tools.mcp import (
    MCPConnection,
    MCPPromptWrapper,
    MCPResourceWrapper,
    MCPToolWrapper,
)


class _FakeClosedResourceError(Exception):
    pass


_FakeClosedResourceError.__name__ = "ClosedResourceError"


def _make_tool_def(name: str = "demo") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} tool",
        inputSchema={"type": "object", "properties": {}},
    )


def _make_resource_def(name: str = "demores") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        uri=f"file:///{name}",
        description=f"{name} resource",
    )


def _make_prompt_def(name: str = "demoprompt") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=f"{name} prompt", arguments=[])


def _tool_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[mcp_types.TextContent(type="text", text=text)])


def _resource_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        contents=[mcp_types.TextResourceContents(uri="file:///x", text=text)]
    )


def _prompt_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        messages=[SimpleNamespace(content=mcp_types.TextContent(type="text", text=text))]
    )


# ---------------------------------------------------------------------------
# Wrapper -> connection.reconnect_if_stale handshake
# ---------------------------------------------------------------------------


def _stub_connection(sessions: list[object]):
    """Connection shim that swaps `session` and bumps `epoch` on each reconnect.

    `sessions[0]` is current; calling `reconnect_if_stale` advances to the next.
    """
    state = SimpleNamespace(idx=0, epoch=0)
    namespace = SimpleNamespace()

    def _session_getter():
        return sessions[state.idx]

    async def _reconnect(*, after_epoch: int) -> None:
        # Always advance for these tests — the parallel-callers test uses a
        # different stub that respects `after_epoch`.
        if state.idx + 1 < len(sessions):
            state.idx += 1
        state.epoch += 1
        namespace.session = sessions[state.idx]
        namespace.epoch = state.epoch
        namespace.reconnect_calls.append(after_epoch)

    namespace.session = sessions[0]
    namespace.epoch = 0
    namespace.reconnect_if_stale = _reconnect
    namespace.reconnect_calls = []
    return namespace


@pytest.mark.asyncio
async def test_tool_reconnects_then_succeeds_against_new_session():
    """ClosedResourceError -> reconnect -> retry on fresh session -> success."""
    dead = AsyncMock()
    dead.call_tool = AsyncMock(side_effect=_FakeClosedResourceError("stream gone"))
    fresh = AsyncMock()
    fresh.call_tool = AsyncMock(return_value=_tool_result("recovered"))

    conn = _stub_connection([dead, fresh])
    wrapper = MCPToolWrapper(conn, "srv", _make_tool_def(), tool_timeout=5)

    output = await wrapper.execute(x=1)

    assert output == "recovered"
    assert dead.call_tool.call_count == 1
    assert fresh.call_tool.call_count == 1
    assert conn.reconnect_calls == [0]  # called once with the pre-failure epoch


@pytest.mark.asyncio
async def test_resource_reconnects_then_succeeds():
    dead = AsyncMock()
    dead.read_resource = AsyncMock(side_effect=_FakeClosedResourceError("gone"))
    fresh = AsyncMock()
    fresh.read_resource = AsyncMock(return_value=_resource_result("data"))

    conn = _stub_connection([dead, fresh])
    wrapper = MCPResourceWrapper(conn, "srv", _make_resource_def())

    output = await wrapper.execute()

    assert output == "data"
    assert conn.reconnect_calls == [0]


@pytest.mark.asyncio
async def test_prompt_reconnects_then_succeeds():
    dead = AsyncMock()
    dead.get_prompt = AsyncMock(side_effect=_FakeClosedResourceError("gone"))
    fresh = AsyncMock()
    fresh.get_prompt = AsyncMock(return_value=_prompt_result("hello"))

    conn = _stub_connection([dead, fresh])
    wrapper = MCPPromptWrapper(conn, "srv", _make_prompt_def())

    output = await wrapper.execute()

    assert output == "hello"
    assert conn.reconnect_calls == [0]


@pytest.mark.asyncio
async def test_wrapper_does_not_reconnect_on_non_transient_error():
    session = AsyncMock()
    session.call_tool = AsyncMock(side_effect=ValueError("nope"))

    conn = _stub_connection([session])
    wrapper = MCPToolWrapper(conn, "srv", _make_tool_def(), tool_timeout=5)

    output = await wrapper.execute()

    assert "ValueError" in output
    assert conn.reconnect_calls == []  # never attempted


@pytest.mark.asyncio
async def test_reconnect_failure_returns_distinct_error_message():
    session = AsyncMock()
    session.call_tool = AsyncMock(side_effect=_FakeClosedResourceError("gone"))

    conn = SimpleNamespace(
        session=session,
        epoch=0,
        reconnect_if_stale=AsyncMock(side_effect=RuntimeError("could not reopen")),
    )
    wrapper = MCPToolWrapper(conn, "srv", _make_tool_def(), tool_timeout=5)

    output = await wrapper.execute()

    assert "reconnect error" in output
    assert "RuntimeError" in output
    # The wrapper must not fall through to a second `call_tool` after a
    # reconnect failure — otherwise we'd loop on the same dead session.
    assert session.call_tool.call_count == 1


@pytest.mark.asyncio
async def test_second_transient_after_reconnect_fails_with_retry_message():
    """Reconnect succeeds but the *next* call also hits a transient — give up."""
    s1 = AsyncMock()
    s1.call_tool = AsyncMock(side_effect=_FakeClosedResourceError("first"))
    s2 = AsyncMock()
    s2.call_tool = AsyncMock(side_effect=_FakeClosedResourceError("still broken"))

    conn = _stub_connection([s1, s2])
    wrapper = MCPToolWrapper(conn, "srv", _make_tool_def(), tool_timeout=5)

    output = await wrapper.execute()

    assert "failed after retry" in output
    assert "ClosedResourceError" in output
    # Reconnect was attempted once; the wrapper does not loop indefinitely.
    assert len(conn.reconnect_calls) == 1


# ---------------------------------------------------------------------------
# MCPConnection.reconnect_if_stale: epoch gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_if_stale_skips_when_epoch_advanced(monkeypatch):
    """If a peer already reconnected past `after_epoch`, this call is a no-op."""
    cfg = SimpleNamespace(auth=None)
    conn = MCPConnection("srv", cfg)
    open_calls = 0

    async def _fake_open(self):
        nonlocal open_calls
        open_calls += 1
        self._session = MagicMock()
        self._stack = MagicMock()
        # _close on a MagicMock stack would call .aclose() which is a MagicMock
        # by default — make it awaitable.
        self._stack.aclose = AsyncMock()

    monkeypatch.setattr(MCPConnection, "_open", _fake_open)

    await conn.connect()
    assert conn.epoch == 1
    assert open_calls == 1

    # Simulate a peer reconnecting between failure and our reconnect call:
    # caller's `after_epoch=0` is now stale (current epoch is 1).
    await conn.reconnect_if_stale(after_epoch=0)
    assert open_calls == 1  # NO rebuild — peer already reconnected
    assert conn.epoch == 1  # unchanged


@pytest.mark.asyncio
async def test_reconnect_if_stale_rebuilds_when_epoch_matches(monkeypatch):
    cfg = SimpleNamespace(auth=None)
    conn = MCPConnection("srv", cfg)
    open_calls = 0

    async def _fake_open(self):
        nonlocal open_calls
        open_calls += 1
        self._session = MagicMock()
        self._stack = MagicMock()
        self._stack.aclose = AsyncMock()

    monkeypatch.setattr(MCPConnection, "_open", _fake_open)

    await conn.connect()
    assert conn.epoch == 1

    # Caller observed epoch 1 before failure — matches current; rebuild.
    await conn.reconnect_if_stale(after_epoch=1)
    assert open_calls == 2
    assert conn.epoch == 2


@pytest.mark.asyncio
async def test_reconnect_serializes_concurrent_callers(monkeypatch):
    """Many parallel ClosedResourceErrors → exactly one rebuild."""
    cfg = SimpleNamespace(auth=None)
    conn = MCPConnection("srv", cfg)
    open_calls = 0
    in_flight = asyncio.Event()
    proceed = asyncio.Event()

    async def _fake_open(self):
        nonlocal open_calls
        open_calls += 1
        self._session = MagicMock()
        self._stack = MagicMock()
        self._stack.aclose = AsyncMock()
        # First reconnect: pause inside the lock so peer callers stack up.
        if open_calls == 2:  # first reconnect (after initial connect)
            in_flight.set()
            await proceed.wait()

    monkeypatch.setattr(MCPConnection, "_open", _fake_open)

    await conn.connect()
    assert conn.epoch == 1

    # All five callers observed epoch 1 before failure; they should serialize
    # on the lock, the first rebuilds, the rest see epoch advanced and skip.
    async def attempt():
        await conn.reconnect_if_stale(after_epoch=1)

    tasks = [asyncio.create_task(attempt()) for _ in range(5)]
    await in_flight.wait()  # first rebuild is mid-flight
    proceed.set()  # let it finish
    await asyncio.gather(*tasks)

    # initial connect (1) + exactly one rebuild (2) = 2 total opens
    assert open_calls == 2
    assert conn.epoch == 2


@pytest.mark.asyncio
async def test_session_property_raises_when_not_connected():
    cfg = SimpleNamespace(auth=None)
    conn = MCPConnection("srv", cfg)

    with pytest.raises(RuntimeError, match="not connected"):
        _ = conn.session


@pytest.mark.asyncio
async def test_aclose_is_idempotent(monkeypatch):
    cfg = SimpleNamespace(auth=None)
    conn = MCPConnection("srv", cfg)

    async def _fake_open(self):
        self._session = MagicMock()
        self._stack = MagicMock()
        self._stack.aclose = AsyncMock()

    monkeypatch.setattr(MCPConnection, "_open", _fake_open)

    await conn.connect()
    await conn.aclose()
    await conn.aclose()  # should not raise on second close
