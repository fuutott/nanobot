"""MCP Streamable HTTP API channel."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import Field
import uvicorn

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Base


class MCPServerConfig(Base):
    """MCP Streamable HTTP API channel configuration."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 18793
    api_keys: dict[str, str] = Field(default_factory=dict)
    allow_from: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    request_timeout_seconds: int = 120
    session_ttl_seconds: int = 3600
    enable_resumption: bool = False
    default_protocol_version: str = "2025-03-26"


# Backward-compatible alias for existing imports.
MCPServerChannelConfig = MCPServerConfig


@dataclass
class _SessionState:
    principal: str
    protocol_version: str
    initialized: bool = False
    streams: set[asyncio.Queue[str]] = field(default_factory=set)
    in_flight: dict[str, asyncio.Task[Any]] = field(default_factory=dict)


class MCPServerChannel(BaseChannel):
    """Expose nanobot as an MCP server over Streamable HTTP."""

    name = "mcpserver"
    display_name = "MCP Server"

    @classmethod
    def default_config(cls) -> dict[str, object]:
        return MCPServerConfig().model_dump(by_alias=True)

    def __init__(self, config: MCPServerConfig | dict[str, object], bus: MessageBus):
        if isinstance(config, dict):
            config = MCPServerConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: MCPServerConfig = config
        self._app: FastAPI | None = None
        self._server: uvicorn.Server | None = None
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._sessions: dict[str, _SessionState] = {}

    async def start(self) -> None:
        """Start the MCP Streamable HTTP server."""
        self._running = True
        if not self._auth_map():
            raise RuntimeError(
                "mcpserver requires authentication: set channels.mcpserver.apiKeys"
            )

        self._app = FastAPI(title="nanobot MCP Server", version="1.0")
        self._register_routes(self._app)

        logger.info("Starting MCP Server channel on http://{}:{}", self.config.host, self.config.port)

        uv_cfg = uvicorn.Config(
            app=self._app,
            host=self.config.host,
            port=self.config.port,
            log_level="info",
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(uv_cfg)
        await self._server.serve()

    async def stop(self) -> None:
        """Stop the MCP server and clear pending operations."""
        self._running = False
        if self._server:
            self._server.should_exit = True

        for req_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.cancel()
            self._pending.pop(req_id, None)

        for state in self._sessions.values():
            for task in state.in_flight.values():
                task.cancel()
            state.in_flight.clear()
            for queue in list(state.streams):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait("")
            state.streams.clear()

    async def send(self, msg: OutboundMessage) -> None:
        """Handle outbound messages for MCP tool calls and progress events."""
        metadata = msg.metadata or {}
        request_id = str(metadata.get("request_id", ""))
        if not request_id:
            logger.warning("mcpserver: outbound message missing request_id metadata")
            return

        session_id = str(metadata.get("mcp_session_id", "")).strip()
        if metadata.get("_progress"):
            progress_token = metadata.get("mcp_progress_token")
            if session_id and progress_token is not None:
                await self._emit_progress(
                    session_id=session_id,
                    progress_token=progress_token,
                    message=msg.content,
                )
            return

        future = self._pending.pop(request_id, None)
        if not future:
            logger.warning("mcpserver: no pending request for id={}", request_id)
            return

        if not future.done():
            future.set_result(msg.content)

    def is_allowed(self, sender_id: str) -> bool:
        """Allowlist check for composite sender aliases."""
        if super().is_allowed(sender_id):
            return True

        allow_list = getattr(self.config, "allow_from", [])
        if not allow_list or "*" in allow_list:
            return False

        parts = [p.strip() for p in str(sender_id).split("|") if p.strip()]
        return any(part in allow_list for part in parts)

    def _register_routes(self, app: FastAPI) -> None:
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
            try:
                principal = self._authenticate_request(request)
                self._validate_origin(request)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

            request.state.auth_principal = principal
            return await call_next(request)

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/mcp")
        async def mcp_sse(request: Request) -> StreamingResponse:
            session_id = self._resolve_session_id(request)
            state = self._session_state(session_id, request)
            queue: asyncio.Queue[str] = asyncio.Queue()
            state.streams.add(queue)

            async def event_stream() -> Any:
                try:
                    hello = {
                        "jsonrpc": "2.0",
                        "method": "notifications/message",
                        "params": {"level": "info", "data": "mcp stream opened"},
                    }
                    yield self._sse_data(hello)
                    while self._running:
                        try:
                            payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                        except asyncio.TimeoutError:
                            # Keepalive comment to prevent idle disconnects.
                            yield ": keepalive\n\n"
                            continue

                        if not payload:
                            break
                        yield payload
                finally:
                    state.streams.discard(queue)

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        @app.delete("/mcp")
        async def delete_session(request: Request) -> JSONResponse:
            session_id = self._header(request, "MCP-Session-Id").strip()
            if not session_id:
                raise HTTPException(status_code=400, detail="missing MCP-Session-Id header")
            self._sessions.pop(session_id, None)
            return JSONResponse(status_code=204, content=None)

        @app.post("/mcp")
        async def mcp_post(payload: dict[str, Any], request: Request) -> JSONResponse:
            if payload.get("jsonrpc") != "2.0":
                raise HTTPException(status_code=400, detail="jsonrpc must be '2.0'")

            method = payload.get("method")
            request_id = payload.get("id")

            if not method:
                # Client response payload is accepted but ignored for v1.
                return JSONResponse(status_code=202, content={"accepted": True})

            if request_id is None:
                # Notifications do not produce responses.
                await self._handle_notification(payload, request)
                return JSONResponse(status_code=202, content={"accepted": True})

            response = await self._handle_request(payload, request)
            response_headers = None
            if isinstance(response, dict):
                response_headers = response.pop("_headers", None)
            return JSONResponse(content=response, headers=response_headers)

    def _auth_map(self) -> dict[str, str]:
        return dict(self.config.api_keys or {})

    def _authenticate_request(self, request: Request) -> str:
        auth_map = self._auth_map()
        if not auth_map:
            raise HTTPException(status_code=500, detail="mcpserver auth is not configured")

        auth = self._header(request, "authorization")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")

        token = auth.removeprefix("Bearer ").strip()
        principal = auth_map.get(token)
        if not principal:
            raise HTTPException(status_code=401, detail="invalid api key")
        return principal

    def _validate_origin(self, request: Request) -> None:
        allowed = set(self.config.allowed_origins or [])
        if not allowed:
            return

        origin = self._header(request, "origin")
        if not origin:
            return
        if origin not in allowed:
            raise HTTPException(status_code=403, detail="origin not allowed")

    def _resolve_session_id(self, request: Request) -> str:
        session_id = self._header(request, "MCP-Session-Id").strip()
        if session_id:
            return session_id
        return uuid.uuid4().hex

    def _session_state(self, session_id: str, request: Request) -> _SessionState:
        state = self._sessions.get(session_id)
        if state:
            return state

        principal = self._sender_id(request)
        protocol = self._header(request, "MCP-Protocol-Version").strip() or self.config.default_protocol_version
        state = _SessionState(principal=principal, protocol_version=protocol)
        self._sessions[session_id] = state
        return state

    @staticmethod
    def _header(request: Request, name: str) -> str:
        """Read request headers with case-insensitive fallback for tests."""
        value = request.headers.get(name)
        if value is not None:
            return str(value)
        lower = name.lower()
        value = request.headers.get(lower)
        if value is not None:
            return str(value)
        for key, val in request.headers.items():
            if str(key).lower() == lower:
                return str(val)
        return ""

    def _sender_id(self, request: Request) -> str:
        principal = getattr(request.state, "auth_principal", "")
        if isinstance(principal, str) and principal.strip():
            principal_str = principal.strip()
            if ":" in principal_str:
                _, suffix = principal_str.split(":", 1)
                if suffix:
                    return f"{principal_str}|{suffix}"
            return principal_str

        raise HTTPException(status_code=401, detail="unauthenticated request")

    def _allow_sender_id(self, request: Request, payload: dict[str, Any]) -> str:
        principal = self._sender_id(request)
        aliases: list[str] = [principal]

        conversation_id = payload.get("conversationId")
        if isinstance(conversation_id, str) and conversation_id.strip():
            aliases.append(conversation_id.strip())

        session_id = payload.get("sessionId")
        if isinstance(session_id, str) and session_id.strip():
            aliases.append(session_id.strip())

        client_host = request.client.host if request.client else ""
        if client_host:
            aliases.extend([f"http:{client_host}", client_host])

        seen: set[str] = set()
        unique = []
        for item in aliases:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return "|".join(unique)

    @staticmethod
    def _jsonrpc_result(request_id: Any, result: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        if headers:
            response["_headers"] = headers
        return response

    @staticmethod
    def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    @staticmethod
    def _sse_data(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\\n\\n"

    async def _handle_notification(self, payload: dict[str, Any], request: Request) -> None:
        method = str(payload.get("method") or "")
        params = payload.get("params") or {}

        if method == "notifications/initialized":
            session_id = self._resolve_session_id(request)
            self._session_state(session_id, request).initialized = True
            return

        if method == "notifications/cancelled":
            session_id = self._header(request, "MCP-Session-Id").strip()
            if not session_id:
                return
            state = self._sessions.get(session_id)
            if not state:
                return
            request_id = str(params.get("requestId", "")).strip()
            if not request_id:
                return
            task = state.in_flight.pop(request_id, None)
            if task:
                task.cancel()
            return

    async def _handle_request(self, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        method = str(payload.get("method") or "")
        request_id = payload.get("id")
        params = payload.get("params") or {}

        try:
            if method == "initialize":
                session_id = self._resolve_session_id(request)
                state = self._session_state(session_id, request)
                result = {
                    "protocolVersion": state.protocol_version,
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "nanobot-mcpserver",
                        "version": "0.1.0",
                    },
                }
                return self._jsonrpc_result(
                    request_id,
                    result,
                    headers={"MCP-Session-Id": session_id, "MCP-Protocol-Version": state.protocol_version},
                )

            session_id = self._header(request, "MCP-Session-Id").strip()
            if not session_id:
                return self._jsonrpc_error(request_id, -32000, "missing MCP-Session-Id header")
            state = self._session_state(session_id, request)

            if method == "ping":
                return self._jsonrpc_result(request_id, {})

            if method == "tools/list":
                return self._jsonrpc_result(request_id, {"tools": [self._agent_chat_tool_spec()]})

            if method == "tools/call":
                task = asyncio.create_task(self._handle_tools_call(request_id, params, request, session_id))
                state.in_flight[str(request_id)] = task
                try:
                    result = await task
                    return self._jsonrpc_result(request_id, result)
                except asyncio.CancelledError:
                    return self._jsonrpc_error(request_id, -32800, "request cancelled")
                finally:
                    state.in_flight.pop(str(request_id), None)

            return self._jsonrpc_error(request_id, -32601, f"method not found: {method}")
        except HTTPException as exc:
            return self._jsonrpc_error(request_id, -32000, exc.detail)
        except Exception as exc:  # pragma: no cover
            logger.exception("mcpserver request failed")
            return self._jsonrpc_error(request_id, -32603, f"internal error: {type(exc).__name__}: {exc}")

    async def _handle_tools_call(
        self,
        rpc_request_id: Any,
        params: dict[str, Any],
        request: Request,
        session_id: str,
    ) -> dict[str, Any]:
        tool_name = str(params.get("name") or "")
        if tool_name != "agent_chat":
            raise HTTPException(status_code=400, detail=f"unsupported tool: {tool_name}")

        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise HTTPException(status_code=400, detail="tools/call.arguments must be an object")

        message = arguments.get("message")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(status_code=400, detail="agent_chat requires a non-empty message")

        sender_id = self._allow_sender_id(request, arguments)
        if not self.is_allowed(sender_id):
            raise HTTPException(status_code=403, detail="sender not allowed")

        conv = arguments.get("conversationId")
        sess = arguments.get("sessionId")
        chat_id = "mcp"
        if isinstance(conv, str) and conv.strip():
            chat_id = conv.strip()
        elif isinstance(sess, str) and sess.strip():
            chat_id = sess.strip()

        progress_token = ((params.get("_meta") or {}).get("progressToken"))

        bus_request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending[bus_request_id] = fut

        metadata = {
            "request_id": bus_request_id,
            "mcp_session_id": session_id,
            "mcp_rpc_request_id": str(rpc_request_id),
        }
        if progress_token is not None:
            metadata["mcp_progress_token"] = progress_token

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=message.strip(),
            metadata=metadata,
            session_key=f"mcp:{session_id}:{chat_id}",
        )

        try:
            content = await asyncio.wait_for(fut, timeout=self.config.request_timeout_seconds)
        except asyncio.TimeoutError:
            self._pending.pop(bus_request_id, None)
            raise HTTPException(status_code=504, detail="agent response timeout")

        return {
            "content": [
                {
                    "type": "text",
                    "text": content,
                }
            ],
            "isError": False,
        }

    async def _emit_progress(self, session_id: str, progress_token: Any, message: str) -> None:
        state = self._sessions.get(session_id)
        if not state or not state.streams:
            return

        now_ms = int(time.time() * 1000)
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {
                "progressToken": progress_token,
                "progress": None,
                "total": None,
                "message": message,
                "timestamp": now_ms,
            },
        }
        raw = self._sse_data(payload)
        for stream in list(state.streams):
            with contextlib.suppress(asyncio.QueueFull):
                stream.put_nowait(raw)

    @staticmethod
    def _agent_chat_tool_spec() -> dict[str, Any]:
        return {
            "name": "agent_chat",
            "description": "Run a chat turn through nanobot and return text output.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "sessionId": {"type": "string"},
                    "conversationId": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                "required": ["message"],
                "additionalProperties": True,
            },
        }
