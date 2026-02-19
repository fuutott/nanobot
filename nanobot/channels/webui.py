"""Web UI channel  browser-based chat interface.

All chat history is stored in the browser via localStorage.
The server only handles real-time message transport over WebSockets.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Request
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
import uvicorn

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import WebUIConfig


_RESOURCES = Path(__file__).parent / "resources"


def _load_ui(title: str) -> str:
    """Read webui.html from the resources directory and substitute {{TITLE}}."""
    template = (_RESOURCES / "webui.html").read_text(encoding="utf-8")
    return template.replace("{{TITLE}}", title)


def randomhex(n: int = 16) -> str:
    """Return a random hex string of length n."""
    return os.urandom(n).hex()


class WebUIChannel(BaseChannel):
    """Browser-based chat UI served over HTTP + WebSocket.

    Chat history is stored entirely in the browser (localStorage).
    The server only routes real-time messages over WebSocket connections.
    """

    name = "webui"

    def __init__(self, config: WebUIConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: WebUIConfig = config
        self._app: FastAPI | None = None
        self._server: uvicorn.Server | None = None
        # Active WebSocket connections keyed by chat_id
        self._connections: dict[str, WebSocket] = {}
        # Valid session tokens (empty = auth disabled)
        self._tokens: set[str] = set()

    # ------------------------------------------------------------------
    # BaseChannel interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Web UI HTTP/WS server."""
        self._running = True
        self._app = FastAPI(title="nanobot Web UI")
        self._register_routes(self._app)

        logger.info(
            f"Starting Web UI channel on http://{self.config.host}:{self.config.port}"
        )

        uv_cfg = uvicorn.Config(
            app=self._app,
            host=self.config.host,
            port=self.config.port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(uv_cfg)
        await self._server.serve()

    async def stop(self) -> None:
        """Stop the Web UI server and close all open WebSocket connections."""
        self._running = False

        for ws in list(self._connections.values()):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()

        if self._server:
            self._server.should_exit = True

    async def send(self, msg: OutboundMessage) -> None:
        """Push an outbound agent message to the matching browser tab."""
        ws = self._connections.get(msg.chat_id)
        if not ws:
            logger.warning(f"webui: no active WebSocket for chat_id={msg.chat_id}")
            return
        try:
            await ws.send_text(
                json.dumps(
                    {"type": "message", "content": msg.content, "chat_id": msg.chat_id},
                    ensure_ascii=False,
                )
            )
        except Exception as e:
            logger.error(f"webui: error sending to WebSocket: {e}")
            self._connections.pop(msg.chat_id, None)

    def _auth_enabled(self) -> bool:
        return bool(self.config.username and self.config.password)

    def _check_token(self, token: str | None) -> bool:
        if not self._auth_enabled():
            return True
        return bool(token and token in self._tokens)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _register_routes(self, app: FastAPI) -> None:
        title = self.config.title

        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:  # type: ignore[return]
            return _load_ui(title)

        @app.post("/login")
        async def login(request: Request) -> JSONResponse:
            if not self._auth_enabled():
                return JSONResponse({"token": ""})
            body = await request.json()
            if (
                body.get("username") == self.config.username
                and body.get("password") == self.config.password
            ):
                token = randomhex(32)
                self._tokens.add(token)
                return JSONResponse({"token": token})
            return JSONResponse({"error": "Invalid credentials"}, status_code=401)

        @app.post("/upload")
        async def upload(request: Request, file: UploadFile | None = File(None)) -> JSONResponse:
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not self._check_token(token or None):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if file is None:
                return JSONResponse({"error": "No file provided"}, status_code=422)
            media_dir = Path.home() / ".nanobot" / "media"
            media_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(file.filename or "file").suffix or ""
            dest = media_dir / f"{randomhex()}{suffix}"
            data = await file.read()
            dest.write_bytes(data)
            logger.debug(f"webui: uploaded {file.filename} -> {dest}")
            return JSONResponse({"path": str(dest)})

        @app.websocket("/ws/{chat_id}")
        async def websocket_endpoint(websocket: WebSocket, chat_id: str, token: str = "") -> None:
            client_host = websocket.client.host if websocket.client else "unknown"
            sender_id = f"web:{client_host}"

            if not self.is_allowed(sender_id):
                await websocket.close(code=1008)  # Policy violation
                return

            if not self._check_token(token or None):
                await websocket.close(code=1008)  # Auth failure
                return

            await websocket.accept()
            self._connections[chat_id] = websocket
            logger.debug(
                f"webui: WebSocket connected chat_id={chat_id} client={client_host}"
            )

            try:
                while True:
                    raw = await websocket.receive_text()
                    data = json.loads(raw)
                    if data.get("type") == "message":
                        content = str(data.get("content", "")).strip()
                        media = [p for p in (data.get("media_paths") or []) if isinstance(p, str)]
                        if content or media:
                            await self._handle_message(
                                sender_id=sender_id,
                                chat_id=chat_id,
                                content=content or "[file]",
                                media=media,
                            )
            except WebSocketDisconnect:
                logger.debug(f"webui: WebSocket disconnected chat_id={chat_id}")
            except Exception as e:
                logger.error(f"webui: WebSocket error for chat_id={chat_id}: {e}")
            finally:
                self._connections.pop(chat_id, None)
