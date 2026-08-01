"""MCP OAuth authentication CLI command (fork-local).

Spec-compliant OAuth 2.1 setup for remote MCP servers: endpoint auto-discovery
(`/.well-known/oauth-authorization-server`), dynamic client registration
(RFC 7591), and device-code / client-credentials token acquisition. The OAuth
machinery lives in ``nanobot/agent/tools/oauth_flow.py`` + ``oauth_tokens.py``;
this module is just the interactive CLI front-end.

Kept in its own module (mirroring upstream's per-area CLI split, e.g.
``cli/provider.py`` / ``cli/webui.py``) so ``commands.py`` stays close to
upstream's slim shape and future CLI-area syncs don't conflict on our block.
Registered by ``commands.py`` via ``app.command("mcp-auth")(mcp_auth)``.
"""

from __future__ import annotations

import typer
from rich.console import Console

from nanobot import __logo__

console = Console()


def mcp_auth(
    server_name: str = typer.Argument(..., help="MCP server name from config"),
    flow: str = typer.Option("", "--flow", "-f", help="Override flow: device_code, client_credentials"),
    force: bool = typer.Option(False, "--force", help="Force re-authentication even if token exists"),
):
    """Authenticate an MCP server using OAuth (MCP spec-compliant).

    device_code is the default — works in containers, headless, NAT, any setup.
    client_credentials is for daemon-to-service (no user interaction).

    Endpoints are auto-discovered via /.well-known/oauth-authorization-server.
    Client is auto-registered via dynamic registration (RFC 7591).

    Example:
        nanobot mcp-auth ms-graph
        nanobot mcp-auth ms-graph --flow client_credentials
    """
    import asyncio as _asyncio

    from nanobot.config.loader import load_config

    config = load_config()
    mcp_servers = config.tools.mcp_servers

    if server_name not in mcp_servers:
        available = ", ".join(mcp_servers.keys()) if mcp_servers else "(none configured)"
        console.print(f"[red]MCP server '{server_name}' not found.[/red]  Configured: {available}")
        raise typer.Exit(1)

    cfg = mcp_servers[server_name]

    if not cfg.auth:
        console.print(f"[red]MCP server '{server_name}' has no auth configuration.[/red]")
        console.print("Add an [cyan]auth[/cyan] block to the server config in nanobot.yaml.")
        raise typer.Exit(1)

    if not cfg.url:
        console.print(f"[red]MCP server '{server_name}' has no URL configured.[/red]")
        console.print("OAuth requires an HTTP-based MCP server URL.")
        raise typer.Exit(1)

    use_flow = flow or cfg.auth.flow

    console.print(f"{__logo__} MCP OAuth Authentication\n")
    console.print(f"Server: [cyan]{server_name}[/cyan]")
    console.print(f"Flow:   [cyan]{use_flow}[/cyan]")

    if use_flow == "client_credentials":
        _asyncio.run(_mcp_auth_client_credentials(server_name, cfg))
    elif use_flow == "device_code":
        _asyncio.run(_mcp_auth_device_code(server_name, cfg, force))
    else:
        console.print(f"[red]Unknown flow: {use_flow}[/red]  Supported: device_code, client_credentials")
        raise typer.Exit(1)


async def _mcp_discover_and_register(server_name: str, cfg) -> dict:
    """Discover OAuth metadata and register client dynamically.

    Returns dict with discovered endpoints and client info.
    """
    from nanobot.agent.tools.oauth_flow import (
        _authorization_base_url,
        _get_endpoints,
        _resolve_env,
        discover_oauth_metadata,
        load_registered_client,
        register_client,
        save_registered_client,
    )

    console.print("\n[dim]Discovering OAuth endpoints...[/dim]")
    base_url = _authorization_base_url(cfg.url)
    metadata = await discover_oauth_metadata(cfg.url)
    endpoints = _get_endpoints(metadata, base_url)

    token_endpoint = cfg.auth.token_endpoint or endpoints["token_endpoint"]
    registration_endpoint = cfg.auth.registration_endpoint or endpoints.get("registration_endpoint", "")
    device_endpoint = cfg.auth.device_authorization_endpoint or endpoints.get("device_authorization_endpoint", "")

    console.print(f"  Token:     [dim]{token_endpoint}[/dim]")
    if device_endpoint:
        console.print(f"  Device:    [dim]{device_endpoint}[/dim]")

    # Get or register client
    client_id = cfg.auth.client_id
    client_secret = _resolve_env(cfg.auth.client_secret) if cfg.auth.client_secret else None

    if not client_id:
        reg = load_registered_client(server_name)
        if reg:
            client_id = reg.client_id
            console.print(f"  Client:    [dim]{client_id} (registered)[/dim]")
        elif registration_endpoint:
            console.print(f"  Registering via [dim]{registration_endpoint}[/dim]...")
            reg = await register_client(
                registration_endpoint=registration_endpoint,
                scopes=cfg.auth.scopes,
            )
            save_registered_client(server_name, reg)
            client_id = reg.client_id
            if reg.client_secret:
                client_secret = reg.client_secret
            console.print(f"  Client:    [green]{client_id}[/green] (new)")
        else:
            console.print("[red]No client_id configured and no registration endpoint found.[/red]")
            raise typer.Exit(1)
    else:
        console.print(f"  Client:    [dim]{client_id} (config)[/dim]")

    return {
        "token_endpoint": token_endpoint,
        "device_endpoint": device_endpoint,
        "client_id": client_id,
        "client_secret": client_secret,
    }


async def _mcp_auth_client_credentials(server_name: str, cfg) -> None:
    """Handle client_credentials flow."""
    from nanobot.agent.tools.oauth_flow import (
        get_client_credentials_token,
        store_token_result,
    )

    endpoints = await _mcp_discover_and_register(server_name, cfg)

    if not endpoints["client_id"] or not endpoints["client_secret"]:
        console.print("[red]client_credentials requires client_id and client_secret[/red]")
        raise typer.Exit(1)

    try:
        result = await get_client_credentials_token(
            token_endpoint=endpoints["token_endpoint"],
            client_id=endpoints["client_id"],
            client_secret=endpoints["client_secret"],
            scopes=cfg.auth.scopes,
        )
        store_token_result(server_name, result)
        console.print(f"\n[green]✓ Token obtained for '{server_name}'[/green]")
        console.print(f"  Expires in: {result.expires_in}s")
    except Exception as e:
        console.print(f"[red]✗ Failed to obtain token: {e}[/red]")
        raise typer.Exit(1)


async def _mcp_auth_device_code(server_name: str, cfg, force: bool) -> None:
    """Handle device_code flow interactively."""
    import asyncio

    from nanobot.agent.tools.oauth_flow import (
        PendingError,
        SlowDownError,
        poll_device_token,
        start_device_code_flow,
        store_token_result,
    )
    from nanobot.agent.tools.oauth_tokens import is_token_valid

    if is_token_valid(server_name) and not force:
        console.print("[green]✓ Valid token already exists.[/green] Use --force to re-authenticate.")
        return

    endpoints = await _mcp_discover_and_register(server_name, cfg)

    device_endpoint = endpoints["device_endpoint"]
    if not device_endpoint:
        console.print("[red]✗ No device authorization endpoint found.[/red]")
        console.print("[dim]Set device_authorization_endpoint in auth config, or check server metadata.[/dim]")
        raise typer.Exit(1)

    try:
        flow_data = await start_device_code_flow(
            device_code_endpoint=device_endpoint,
            client_id=endpoints["client_id"],
            scopes=cfg.auth.scopes,
            client_secret=endpoints["client_secret"],
        )
    except Exception as e:
        console.print(f"[red]✗ Failed to start device code flow: {e}[/red]")
        raise typer.Exit(1)

    console.print("\n[bold]To authenticate, visit:[/bold]")
    console.print(f"  [cyan underline]{flow_data['verification_uri']}[/cyan underline]")
    console.print(f"\nEnter code: [bold yellow]{flow_data['user_code']}[/bold yellow]")
    if flow_data.get("verification_uri_complete"):
        console.print("\n[dim]Or open this pre-filled link directly:[/dim]")
        console.print(f"  [cyan underline]{flow_data['verification_uri_complete']}[/cyan underline]")
    console.print("\n[dim]Waiting for authentication...[/dim]")

    interval = flow_data["interval"]
    device_code = flow_data["device_code"]
    elapsed = 0
    timeout = flow_data.get("expires_in", 900)

    while elapsed < timeout:
        await asyncio.sleep(interval)
        elapsed += interval

        try:
            result = await poll_device_token(
                token_endpoint=endpoints["token_endpoint"],
                device_code=device_code,
                client_id=endpoints["client_id"],
                client_secret=endpoints["client_secret"],
            )
            store_token_result(server_name, result)
            console.print("\n[green]✓ Authenticated successfully![/green]")
            console.print(f"  Token expires in: {result.expires_in}s")
            return
        except PendingError:
            console.print("[dim].[/dim]", end="")
            continue
        except SlowDownError:
            interval += 5
            console.print(f"\n[dim]Slowing down (interval: {interval}s)...[/dim]")
            continue
        except Exception as e:
            error_msg = str(e)
            if "expired" in error_msg.lower():
                console.print("\n[red]✗ Device code expired. Please try again.[/red]")
            else:
                console.print(f"\n[red]✗ Authentication failed: {e}[/red]")
            raise typer.Exit(1)

    console.print("\n[red]✗ Timed out waiting for authentication.[/red]")
    raise typer.Exit(1)
