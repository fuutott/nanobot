"""OAuth token storage for MCP servers.

Tokens are stored as JSON files in ~/.nanobot/oauth_tokens/ with mode 600.
Each file is named after the MCP server config key.
"""

import json
import os
from pathlib import Path
from time import time
from typing import Any

from loguru import logger


def _tokens_dir() -> Path:
    """Return the oauth tokens directory, creating it if needed."""
    d = Path.home() / ".nanobot" / "oauth_tokens"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _token_path(server_name: str) -> Path:
    """Return the token file path for a given server."""
    # Sanitize server name to prevent path traversal
    safe = server_name.replace("/", "_").replace("\\", "_").replace("..", "_")
    return _tokens_dir() / f"{safe}.json"


def _secure_write(path: Path, data: dict[str, Any]) -> None:
    """Write data to a file with mode 600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.rename(path)


def store_token(
    server_name: str,
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int = 3600,
    scope: str | None = None,
) -> None:
    """Store an OAuth token set for a server."""
    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time()) + expires_in,
        "scope": scope,
    }
    _secure_write(_token_path(server_name), data)
    logger.debug("Stored OAuth token for '{}'", server_name)


def get_token(server_name: str) -> str | None:
    """Get the current access token for a server, if valid."""
    path = _token_path(server_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read token for '{}': {}", server_name, e)
        return None
    if data.get("expires_at", 0) < time() + 300:  # 5 min buffer
        return None
    return data.get("access_token")


def get_refresh_token(server_name: str) -> str | None:
    """Get the refresh token for a server."""
    path = _token_path(server_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("refresh_token")


def is_token_valid(server_name: str) -> bool:
    """Check if a valid (non-expired) token exists for a server."""
    return get_token(server_name) is not None


def token_expires_at(server_name: str) -> int | None:
    """Return the expiry timestamp for a server's token, or None."""
    path = _token_path(server_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("expires_at")


def delete_token(server_name: str) -> None:
    """Delete stored token for a server."""
    path = _token_path(server_name)
    if path.exists():
        path.unlink()
        logger.debug("Deleted OAuth token for '{}'", server_name)
