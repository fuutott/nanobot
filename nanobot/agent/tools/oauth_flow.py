"""OAuth 2.1 authentication for MCP servers (spec-compliant).

Implements the MCP authorization specification (2025-03-26):
- Server Metadata Discovery (RFC 8414)
- Dynamic Client Registration (RFC 7591)
- Authorization Code Grant + PKCE (primary)
- Device Code Grant (fallback for headless/container environments)
- Client Credentials Grant (daemon-to-service)
- Token refresh with automatic rotation

Flow:
1. MCP client connects to server → receives HTTP 401
2. Discover auth metadata from /.well-known/oauth-authorization-server
3. Dynamically register client via /register (RFC 7591)
4. Authorization code + PKCE flow (or device_code fallback)
5. Store token, attach Authorization header to all requests
6. Background refresh before expiry
"""

import base64
import hashlib
import json
import os
import secrets
import re
import urllib.parse
from typing import Any

import httpx
from loguru import logger

from nanobot.agent.tools.oauth_tokens import store_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_env(value: str) -> str:
    """Resolve ${VAR_NAME} references in config values."""
    def replacer(match: re.Match) -> str:
        var = match.group(1)
        resolved = os.environ.get(var, "")
        if not resolved:
            logger.warning("Environment variable '{}' not set", var)
        return resolved
    return re.sub(r"\$\{(\w+)\}", replacer, value)


def _authorization_base_url(server_url: str) -> str:
    """Derive the authorization base URL from the MCP server URL.

    Per MCP spec: discard any existing path component.
    e.g. https://api.example.com/v1/mcp → https://api.example.com
    """
    parsed = urllib.parse.urlparse(server_url)
    return f"{parsed.scheme}://{parsed.hostname}" + (
        f":{parsed.port}" if parsed.port else ""
    )


def _generate_code_verifier() -> str:
    """Generate a PKCE code_verifier (43-128 chars, unreserved chars)."""
    return secrets.token_urlsafe(32)


def _generate_code_challenge(verifier: str) -> str:
    """Generate PKCE code_challenge (S256 method)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Token result
# ---------------------------------------------------------------------------

class TokenResult:
    """Parsed OAuth token response."""

    def __init__(self, data: dict[str, Any]):
        self.access_token: str = data["access_token"]
        self.refresh_token: str | None = data.get("refresh_token")
        self.expires_in: int = data.get("expires_in", 3600)
        self.scope: str | None = data.get("scope")
        self.token_type: str = data.get("token_type", "Bearer")
        self.raw = data


def store_token_result(server_name: str, result: TokenResult) -> None:
    """Store a TokenResult for a server."""
    store_token(
        server_name=server_name,
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        scope=result.scope,
    )


# ---------------------------------------------------------------------------
# Metadata Discovery (RFC 8414)
# ---------------------------------------------------------------------------

async def discover_oauth_metadata(
    server_url: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Discover OAuth authorization server metadata.

    GET /.well-known/oauth-authorization-server at the authorization base URL.
    Returns parsed metadata dict or None if discovery fails.
    """
    base = _authorization_base_url(server_url)
    well_known = f"{base}/.well-known/oauth-authorization-server"

    close_after = False
    if http_client is None:
        http_client = httpx.AsyncClient(follow_redirects=True, timeout=10)
        close_after = True

    try:
        resp = await http_client.get(
            well_known,
            headers={"MCP-Protocol-Version": "2025-03-26"},
        )
        if resp.status_code == 200:
            data = resp.json()
            logger.info(
                "OAuth metadata discovered for '{}': token_endpoint={}",
                base,
                data.get("token_endpoint", "(none)"),
            )
            return data
        else:
            logger.debug(
                "OAuth metadata discovery failed: HTTP {}", resp.status_code
            )
            return None
    except Exception as e:
        logger.debug("OAuth metadata discovery error: {}", e)
        return None
    finally:
        if close_after:
            await http_client.aclose()


def _get_endpoints(
    metadata: dict[str, Any] | None,
    base_url: str,
) -> dict[str, str]:
    """Resolve auth endpoints from metadata or fallback defaults.

    Fallback defaults per MCP spec:
    - /authorize
    - /token
    - /register
    - /device_authorization (not in MCP spec, but common for device_code)
    """
    defaults = {
        "token_endpoint": f"{base_url}/token",
        "registration_endpoint": f"{base_url}/register",
        "device_authorization_endpoint": f"{base_url}/device_authorization",
    }
    if metadata is None:
        return defaults

    result = {}
    for key, default in defaults.items():
        result[key] = metadata.get(key, default)
    # Also grab authorization_endpoint if present in metadata
    if "authorization_endpoint" in (metadata or {}):
        result["authorization_endpoint"] = metadata["authorization_endpoint"]
    return result


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------

class RegisteredClient:
    """A client registered with an OAuth server."""

    def __init__(self, data: dict[str, Any]):
        self.client_id: str = data["client_id"]
        self.client_secret: str | None = data.get("client_secret")
        self.raw = data


async def register_client(
    registration_endpoint: str,
    redirect_uris: list[str] | None = None,
    scopes: list[str] | None = None,
    client_name: str = "nanobot",
    http_client: httpx.AsyncClient | None = None,
) -> RegisteredClient:
    """Dynamically register an OAuth client (RFC 7591).

    This allows nanobot to obtain a client_id without manual setup.
    """
    body: dict[str, Any] = {
        "client_name": client_name,
        "grant_types": ["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"],
        "token_endpoint_auth_method": "none",  # public client
    }
    if redirect_uris:
        body["redirect_uris"] = redirect_uris
    if scopes:
        body["scope"] = " ".join(scopes)

    close_after = False
    if http_client is None:
        http_client = httpx.AsyncClient(follow_redirects=True, timeout=10)
        close_after = True

    try:
        resp = await http_client.post(registration_endpoint, json=body)
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "Dynamic client registered: client_id={}", data.get("client_id")
        )
        return RegisteredClient(data)
    finally:
        if close_after:
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Authorization Code + PKCE Flow
# ---------------------------------------------------------------------------

class AuthorizationRequest:
    """State for an in-progress authorization code flow."""

    def __init__(
        self,
        authorization_endpoint: str,
        client_id: str,
        redirect_uri: str,
        scope: str,
        code_verifier: str,
        state: str,
    ):
        self.authorization_endpoint = authorization_endpoint
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.code_verifier = code_verifier
        self.state = state

    @property
    def code_challenge(self) -> str:
        return _generate_code_challenge(self.code_verifier)

    def build_url(self) -> str:
        """Build the authorization URL for the user to visit."""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.authorization_endpoint}?{urllib.parse.urlencode(params)}"


def create_authorization_request(
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str = "http://localhost:8400/callback",
    scopes: list[str] | None = None,
) -> AuthorizationRequest:
    """Create a new authorization code + PKCE request."""
    return AuthorizationRequest(
        authorization_endpoint=authorization_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=" ".join(scopes) if scopes else "",
        code_verifier=_generate_code_verifier(),
        state=secrets.token_urlsafe(16),
    )


async def exchange_authorization_code(
    token_endpoint: str,
    code: str,
    auth_request: AuthorizationRequest,
    client_secret: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TokenResult:
    """Exchange an authorization code for tokens (with PKCE verifier)."""
    body: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": auth_request.redirect_uri,
        "client_id": auth_request.client_id,
        "code_verifier": auth_request.code_verifier,
    }
    if client_secret:
        body["client_secret"] = client_secret

    close_after = False
    if http_client is None:
        http_client = httpx.AsyncClient(follow_redirects=True, timeout=10)
        close_after = True

    try:
        resp = await http_client.post(token_endpoint, data=body)
        resp.raise_for_status()
        return TokenResult(resp.json())
    finally:
        if close_after:
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Device Code Flow (fallback for headless/container)
# ---------------------------------------------------------------------------

async def start_device_code_flow(
    device_code_endpoint: str,
    client_id: str,
    scopes: list[str] | None = None,
    client_secret: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Initiate a device code flow.

    Returns dict with: device_code, user_code, verification_uri, expires_in, interval
    """
    body: dict[str, str] = {
        "client_id": client_id,
    }
    if scopes:
        body["scope"] = " ".join(scopes)
    if client_secret:
        body["client_secret"] = client_secret

    close_after = False
    if http_client is None:
        http_client = httpx.AsyncClient(follow_redirects=True, timeout=10)
        close_after = True

    try:
        resp = await http_client.post(device_code_endpoint, data=body)
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "Device code flow started. user_code={}, uri={}",
            data.get("user_code"),
            data.get("verification_uri"),
        )
        return {
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data["verification_uri"],
            "expires_in": data.get("expires_in", 900),
            "interval": data.get("interval", 5),
        }
    finally:
        if close_after:
            await http_client.aclose()


class PendingError(Exception):
    """User hasn't completed login yet."""


class SlowDownError(Exception):
    """Polling interval too fast."""


async def poll_device_token(
    token_endpoint: str,
    device_code: str,
    client_id: str,
    client_secret: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TokenResult:
    """Poll the token endpoint waiting for user to complete device code login."""
    body: dict[str, str] = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": client_id,
        "device_code": device_code,
    }
    if client_secret:
        body["client_secret"] = client_secret

    close_after = False
    if http_client is None:
        http_client = httpx.AsyncClient(follow_redirects=True, timeout=10)
        close_after = True

    try:
        resp = await http_client.post(token_endpoint, data=body)
        data = resp.json()

        error = data.get("error")
        if error == "authorization_pending":
            raise PendingError("User has not yet completed login")
        elif error == "slow_down":
            raise SlowDownError("Polling too fast")
        elif error:
            raise Exception(
                f"OAuth error: {error} - {data.get('error_description', '')}"
            )

        return TokenResult(data)
    finally:
        if close_after:
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Client Credentials Flow (daemon-to-service)
# ---------------------------------------------------------------------------

async def get_client_credentials_token(
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    scopes: list[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TokenResult:
    """Obtain a token via client_credentials grant (no user interaction)."""
    body: dict[str, str] = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scopes:
        body["scope"] = " ".join(scopes)

    close_after = False
    if http_client is None:
        http_client = httpx.AsyncClient(follow_redirects=True, timeout=10)
        close_after = True

    try:
        resp = await http_client.post(token_endpoint, data=body)
        resp.raise_for_status()
        return TokenResult(resp.json())
    finally:
        if close_after:
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Token Refresh
# ---------------------------------------------------------------------------

async def refresh_access_token(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    client_secret: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TokenResult:
    """Refresh an access token using a refresh token."""
    body: dict[str, str] = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if client_secret:
        body["client_secret"] = client_secret

    close_after = False
    if http_client is None:
        http_client = httpx.AsyncClient(follow_redirects=True, timeout=10)
        close_after = True

    try:
        resp = await http_client.post(token_endpoint, data=body)
        resp.raise_for_status()
        return TokenResult(resp.json())
    finally:
        if close_after:
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Stored client registration
# ---------------------------------------------------------------------------

def _registered_client_path(server_name: str) -> str:
    """Path for storing dynamic client registration."""
    from pathlib import Path
    d = Path.home() / ".nanobot" / "oauth_tokens"
    d.mkdir(parents=True, exist_ok=True)
    safe = server_name.replace("/", "_").replace("\\", "_").replace("..", "_")
    return str(d / f"{safe}.client.json")


def save_registered_client(server_name: str, client: RegisteredClient) -> None:
    """Save dynamic client registration for reuse."""
    import os as _os
    path = _registered_client_path(server_name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(client.raw, f, indent=2)
    _os.chmod(tmp, 0o600)
    _os.rename(tmp, path)
    logger.debug("Saved client registration for '{}'", server_name)


def load_registered_client(server_name: str) -> RegisteredClient | None:
    """Load a previously registered client."""
    path = _registered_client_path(server_name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return RegisteredClient(json.load(f))
    except Exception:
        return None
