"""OpenAI-compatible HTTP API channel."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import Field
import uvicorn

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Base


class OpenAIAPIConfig(Base):
    """OpenAI-compatible HTTP API channel configuration."""

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 18791
    api_keys: dict[str, str] = Field(default_factory=dict)
    allow_from: list[str] = Field(default_factory=list)
    request_timeout_seconds: int = 120


class OpenAIAPIChannel(BaseChannel):
    """Expose nanobot through OpenAI-compatible HTTP endpoints."""

    name = "openaiapi"

    @classmethod
    def default_config(cls) -> dict[str, object]:
        return OpenAIAPIConfig().model_dump(by_alias=True)

    def __init__(self, config: OpenAIAPIConfig | dict[str, object], bus: MessageBus):
        if isinstance(config, dict):
            config = OpenAIAPIConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: OpenAIAPIConfig = config
        self._app: FastAPI | None = None
        self._server: uvicorn.Server | None = None
        self._pending: dict[str, asyncio.Future[str]] = {}

    async def start(self) -> None:
        """Start the OpenAI-compatible HTTP server."""
        self._running = True
        if not self._auth_map():
            raise RuntimeError(
                "openaiapi requires authentication: set channels.openaiapi.apiKeys"
            )
        self._app = FastAPI(title="nanobot OpenAI API", version="1.0")
        self._register_routes(self._app)

        logger.info(f"Starting OpenAI API channel on http://{self.config.host}:{self.config.port}")

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
        """Stop the OpenAI-compatible HTTP server."""
        self._running = False
        if self._server:
            self._server.should_exit = True

        for req_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.cancel()
            self._pending.pop(req_id, None)

    async def send(self, msg: OutboundMessage) -> None:
        """Resolve a pending HTTP request with outbound agent content."""
        request_id = str(msg.metadata.get("request_id", "")) if msg.metadata else ""
        if not request_id:
            logger.warning("openaiapi: outbound message missing request_id metadata")
            return

        future = self._pending.pop(request_id, None)
        if not future:
            logger.warning(f"openaiapi: no pending request for id={request_id}")
            return

        if not future.done():
            future.set_result(msg.content)

    def is_allowed(self, sender_id: str) -> bool:
        """Allowlist check for composite sender aliases.

        sender_id may contain pipe-separated aliases (e.g. principal|user|host).
        Accept when any alias is explicitly allowlisted.
        """
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
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

            request.state.auth_principal = principal
            return await call_next(request)

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/v1/models")
        async def models() -> dict[str, Any]:
            return {
                "object": "list",
                "data": [
                    {
                        "id": "nanobot-agent",
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "nanobot",
                    }
                ],
            }

        @app.post("/v1/chat/completions")
        async def chat_completions(payload: dict[str, Any], request: Request) -> dict[str, Any]:
            stream = bool(payload.get("stream"))

            requested_model = str(payload.get("model") or "nanobot-agent")
            messages = payload.get("messages")
            if not isinstance(messages, list) or not messages:
                raise HTTPException(status_code=400, detail="messages must be a non-empty array")

            history_messages, prompt = self._normalize_messages_for_agent(messages)
            if not prompt:
                raise HTTPException(status_code=400, detail="could not extract text prompt from messages")

            request_id = uuid.uuid4().hex
            sender_id = self._allow_sender_id(request, payload)
            if not self.is_allowed(sender_id):
                raise HTTPException(status_code=403, detail="sender not allowed")

            chat_id = self._chat_id(request, payload)
            metadata = {
                "request_id": request_id,
                "openai_history": history_messages,
            }

            loop = asyncio.get_running_loop()
            fut: asyncio.Future[str] = loop.create_future()
            self._pending[request_id] = fut

            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=prompt,
                metadata=metadata,
            )

            completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

            if stream:
                logger.debug(
                    f"openaiapi: accepted model='{requested_model}' (ignored); using configured provider model"
                )

                async def event_stream() -> Any:
                    try:
                        content = await asyncio.wait_for(
                            fut, timeout=self.config.request_timeout_seconds
                        )
                    except asyncio.TimeoutError:
                        self._pending.pop(request_id, None)
                        error_chunk = {
                            "id": completion_id,
                            "object": "error",
                            "error": {
                                "message": "agent response timeout",
                                "type": "timeout_error",
                            },
                        }
                        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    now = int(time.time())
                    first_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": now,
                        "model": "nanobot-agent",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": content,
                                },
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"

                    final_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": now,
                        "model": "nanobot-agent",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"

                return StreamingResponse(event_stream(), media_type="text/event-stream")

            try:
                content = await asyncio.wait_for(fut, timeout=self.config.request_timeout_seconds)
            except asyncio.TimeoutError:
                self._pending.pop(request_id, None)
                raise HTTPException(status_code=504, detail="agent response timeout")

            logger.debug(
                f"openaiapi: accepted model='{requested_model}' (ignored); using configured provider model"
            )

            now = int(time.time())

            return {
                "id": completion_id,
                "object": "chat.completion",
                "created": now,
                "model": "nanobot-agent",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": content,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }

    def _auth_map(self) -> dict[str, str]:
        """Return accepted API keys and their server-side principal IDs."""
        return dict(self.config.api_keys or {})

    def _authenticate_request(self, request: Request) -> str:
        """Validate Bearer token and return authenticated principal ID."""
        auth_map = self._auth_map()
        if not auth_map:
            raise HTTPException(status_code=500, detail="openaiapi auth is not configured")

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")

        token = auth.removeprefix("Bearer ").strip()
        principal = auth_map.get(token)
        if not principal:
            raise HTTPException(status_code=401, detail="invalid api key")
        return principal

    @staticmethod
    def _message_text(content: Any) -> str:
        """Extract text from OpenAI message content (string or content parts)."""
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()

        return ""

    def _extract_prompt(self, messages: list[dict[str, Any]]) -> str:
        """Extract the latest user text prompt from OpenAI messages."""
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            text = self._message_text(msg.get("content"))
            if text:
                return text

        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            text = self._message_text(msg.get("content"))
            if text:
                return text

        return ""

    def _normalize_messages_for_agent(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, str]], str]:
        """Convert OpenAI chat messages to (history, current_user_prompt) for the agent loop."""
        normalized: list[dict[str, str]] = []

        for raw in messages:
            if not isinstance(raw, dict):
                continue

            role = str(raw.get("role") or "").strip().lower()
            if role == "developer":
                role = "system"
            if role not in {"system", "user", "assistant"}:
                continue

            text = self._message_text(raw.get("content"))
            if not text:
                continue

            normalized.append({"role": role, "content": text})

        if not normalized:
            return [], ""

        for idx in range(len(normalized) - 1, -1, -1):
            if normalized[idx]["role"] == "user":
                history = normalized[:idx]
                current_prompt = normalized[idx]["content"]
                return history, current_prompt

        return normalized[:-1], normalized[-1]["content"]

    def _chat_id(self, request: Request, payload: dict[str, Any]) -> str:
        """Build stable chat_id for session continuity in OpenAI-compatible clients."""
        user = payload.get("user")
        if isinstance(user, str) and user.strip():
            return user.strip()

        for key in ("conversation_id", "session_id", "chat_id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for header in ("x-conversation-id", "x-session-id", "x-chat-id"):
            value = request.headers.get(header, "")
            if value.strip():
                return value.strip()

        client = request.client.host if request.client else "http-client"
        return f"http:{client}"

    def _sender_id(self, request: Request) -> str:
        """Build sender identifier from authenticated server-side principal."""
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
        """Build composite sender aliases for allowFrom matching.

        Order of aliases includes authenticated principal, OpenAI `user`, and
        client host fallback so allowFrom can match documented fields.
        """
        principal = self._sender_id(request)
        aliases: list[str] = [principal]

        user = payload.get("user")
        if isinstance(user, str) and user.strip():
            aliases.append(user.strip())

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
