This file provides guidance to AI coding agents working with this repository.

## Project Overview

nanobot is a lightweight, open-source AI agent framework written in Python with a React/TypeScript WebUI. It centers around a small agent loop that receives messages from chat channels, invokes an LLM provider, executes tools, and manages session memory.

This checkout is a **personal fork of `HKUDS/nanobot`** maintained at `fuutott/nanobot`. The fork adds plugin channels (Web UI, OpenAI API, MCP Server), tighter Docker defaults, MCP OAuth 2.1 support for remote MCP servers, multi-provider routing, per-subagent provider/model override, and dev workflow scripts for WIP syncing. See the "Fork additions" section at the bottom.

## Development Commands

```bash
# Python: run single test / lint
pytest tests/test_openai_api.py::test_function -v
ruff check nanobot/

# WebUI: dev server (proxies API/WS to gateway :8765), build, test
# Build outputs to ../nanobot/web/dist (bundled into the Python wheel)
cd webui && bun run dev      # or NANOBOT_API_URL=... bun run dev
cd webui && bun run build
cd webui && bun run test

# Gateway
nanobot gateway
```

Always test with `uv` (not plain `python` or `pytest`).

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`nanobot/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`nanobot/channels/`) receive messages from external platforms and publish `InboundMessage` events to the bus.
2. **`AgentLoop`** (`nanobot/agent/loop.py`) consumes inbound messages, builds context, and coordinates the turn.
3. **`AgentRunner`** (`nanobot/agent/runner.py`) handles the actual LLM conversation loop: send messages to the provider, receive tool calls, execute tools, and stream responses.
4. Responses are published as `OutboundMessage` events back to the appropriate channel.

### Key Subsystems

- **Agent Loop** (`nanobot/agent/loop.py`, `runner.py`): The core processing engine. `AgentLoop` manages session keys, hooks, and context building. `AgentRunner` executes the multi-turn LLM conversation with tool execution.
- **LLM Providers** (`nanobot/providers/`): Provider implementations (Anthropic, OpenAI-compatible, OpenAI Responses API, Azure, Bedrock, GitHub Copilot, OpenAI Codex, etc.) built on a common base (`base.py`). Includes image generation (`image_generation.py`) and audio transcription (`transcription.py`). `factory.py` and `registry.py` handle instantiation and model discovery.
- **Channels** (`nanobot/channels/`): Platform integrations (Telegram, Discord, Slack, Feishu, Matrix, WhatsApp, QQ, WeChat, WeCom, DingTalk, Email, MoChat, MS Teams, WebSocket, Mattermost). `manager.py` discovers and coordinates them. Channels are self-contained packages auto-discovered via `pkgutil` scanning.
- **Tools** (`nanobot/agent/tools/`): Agent capabilities exposed to the LLM: filesystem (read/write/edit/list), shell execution (with sandbox backends), web search/fetch, MCP servers, cron, notebook editing, subagent spawning, long-running tasks / sustained goals (`long_task.py`), image generation, and self-modification. Tools are auto-discovered via `pkgutil` scan + entry-point plugins.
- **Memory** (`nanobot/agent/memory.py`): Session history persistence with Dream two-phase memory consolidation. Uses atomic writes with fsync for durability.
- **Session Management** (`nanobot/session/`): Per-session history, context compaction, TTL-based auto-compaction (`manager.py`), and sustained goal state tracking (`goal_state.py`).
- **Config** (`nanobot/config/schema.py`, `loader.py`): Pydantic-based configuration loaded from `~/.nanobot/config.json`. Supports camelCase aliases for JSON compatibility.
- **WebUI** (`webui/`): Vite-based React SPA that talks to the gateway over a WebSocket multiplex protocol. The dev server proxies `/api`, `/webui`, `/auth`, and WebSocket traffic to the gateway.
- **API Server** (`nanobot/api/server.py`): OpenAI-compatible HTTP API (`/v1/chat/completions`, `/v1/models`) for programmatic access.
- **Command Router** (`nanobot/command/`): Slash command routing and built-in command handlers.
- **Heartbeat** (`nanobot/templates/HEARTBEAT.md`): Periodic task list checked via `cron` jobs (legacy dedicated service removed).
- **Pairing** (`nanobot/pairing/`): DM sender approval store with persistent pairing codes per channel.
- **Skills** (`nanobot/skills/`): Built-in skill definitions (cron, github, image-generation, etc.) loaded into agent context.
- **Security** (`nanobot/security/`): PTH file guard and other security measures activated at CLI entry.

### Entry Points

- **CLI**: `nanobot/cli/commands.py`
- **Python SDK**: `nanobot/nanobot.py`

## Project-Specific Notes

- Architecture constraints: [`.agent/design.md`](.agent/design.md)
- Security boundaries: [`.agent/security.md`](.agent/security.md)
- Common gotchas: [`.agent/gotchas.md`](.agent/gotchas.md)

## Contribution Flow

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for contribution flow and PR guidelines.

## Code Style

- Python 3.11+, asyncio throughout.
- Line length: 100.
- Linting: `ruff` with rules E, F, I, N, W (E501 ignored).
- pytest with `asyncio_mode = "auto"`.

## Common File Locations

- Config schema: `nanobot/config/schema.py`
- Provider base / new provider template: `nanobot/providers/base.py`
- Channel base / new channel template: `nanobot/channels/base.py`
- Tool registry: `nanobot/agent/tools/registry.py`
- WebUI dev proxy config: `webui/vite.config.ts`
- Tests mirror the `nanobot/` package structure.

---

# Fork additions (`fuutott/nanobot`)

The rest of this file is specific to this personal fork. Upstream contributors can stop here.

## Fork-only subsystems

### Plugin Channels (`plugins/`)

Three fork-authored `BaseChannel` subclasses shipped as separate Python packages:
- `nanobot-channel-webui` — Browser UI (ships its own prebuilt webui dist)
- `nanobot-channel-openaiapi` — HTTP API endpoint
- `nanobot-channel-mcpserver` — MCP server integration

The Dockerfile sets `NANOBOT_SKIP_WEBUI_BUILD=1` to skip upstream's hatch webui build (the webui plugin already has its own dist).

**Discovery (post-`462a0dfb`):** upstream retired the `nanobot.channels` entry-point group these plugins used, in favour of self-contained in-tree channel packages (`nanobot/channels/<name>/manifest.py` exporting `PLUGIN = ChannelPlugin(...)`). `load_channel_package` requires the manifest's `runtime=` target to physically live inside `nanobot/channels/<name>/`. So each plugin now has a thin in-tree shim:
- `nanobot/channels/<name>/__init__.py`
- `nanobot/channels/<name>/manifest.py` — `PLUGIN` with `runtime=f"{__package__}.runtime:<Class>"`, `setup=ChannelSetupSpec(fields={})` (configured via config.json, not the wizard), `settings_visible=False`
- `nanobot/channels/<name>/runtime.py` — re-exports the class from the installed plugin package (`from nanobot_channel_webui.channel import WebUIChannel`), satisfying the in-package check while keeping the plugin (and its dist) as the source of truth

The plugins' own `[project.entry-points."nanobot.channels"]` declarations were removed (the group is dead; discovery is via the in-tree manifests). `tests/channels/test_channel_setup.py::EXPECTED_CHANNELS` includes the 3 fork channels.

**Channel deps at build:** upstream also moved per-channel dependencies out of pyproject extras into each channel's `manifest.py` (`dependencies=`) with a runtime pip auto-installer that can't run in our non-root/offline container. The Dockerfile's `ARG NANOBOT_CHANNEL_DEPS` bakes the enabled channels' deps (discord.py, python-telegram-bot, socks) at build — **keep it in sync with the enabled channels' manifest `dependencies=`** or the channel silently fails to load.

### MCP OAuth 2.1 (`agent/tools/oauth_flow.py`, `oauth_tokens.py`, `agent/loop.py`)

Talk to OAuth-protected remote MCP servers. Device-flow + dynamic client registration. `_resolve_oauth_token` injects `Authorization: Bearer …` at the top of upstream's `connect_single_server`, so it re-resolves on every reconnect (refresh-token rotation happens for free). `_oauth_refresh_loop` refreshes tokens before expiry. CLI command: `nanobot mcp-auth <server-name>`.

### Per-subagent provider/model override (`agent/tools/spawn.py`)

Agent can pass `provider=` and `model=` when calling `spawn`. Defaults inherit from main agent. Agent discovers options via `my check available_providers` / `my check available_models`.

### Docker deployment shape

- Base image: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` with git, bubblewrap, libmagic1, tmux/procps/iputils/dnsutils (no Node.js — the webui plugin ships its own prebuilt dist)
- Runs as non-root user `nanobottie` (upstream renamed its user to `nanobot` + root/setpriv drop for Render in `462a0dfb`/`c7737909`; we keep `nanobottie` + direct `USER`, and upstream's entrypoint takes its "already non-root" branch for us)
- Built-in channel extras: `ARG NANOBOT_EXTRAS=discord,telegram` — telegram is an optional extra upstream (`.[telegram]`), so it MUST be listed here or the channel silently fails to load with `No module named 'telegram'`
- Exposed ports: **18790** (gateway), **18791** (openaiapi plugin), **18792** (webui plugin), **18793** (mcpserver plugin), 8765 (optional websocket channel) — all on all interfaces for LAN/Tailscale access (upstream now binds `127.0.0.1:18790` only)
- Resource limits in `docker-compose.yml`: 1 CPU / 1 GB memory per service
- Rebuild via `./redo.ps1` — wraps `docker compose build && docker compose up -d`

## Sync workflow

This is the primary edit machine. Work committed here is pushed to `origin` (`fuutott/nanobot`) and pulled on other machines. Other machines do a plain `git pull` — no force, no reset.

The cardinal rule: **never rewrite commits already on `origin/main`**.

### Sync strategy: merge, not rebase

We keep local customisations on top of `HKUDS/nanobot`. The right tool for syncing is **`git merge upstream/main`**, not `git rebase upstream/main`.

Why merge, not rebase:
- Rebase rewrites local SHAs → requires `--force-with-lease` → breaks every other machine's `git pull`.
- Merge creates a new commit with `upstream/main` as a parent → git forever knows we have those upstream commits → `git log main..upstream/main` only shows truly new work → clean future syncs.
- Fast-forward push to `origin/main` → downstream machines just `git pull`.

### Upstream sync protocol

When the user posts a message matching:

> "This branch is N commits ahead of and M commits behind HKUDS/nanobot:main."

**Execute the full sync automatically:**

1. `git fetch upstream`
2. `git log --oneline main..upstream/main` — review incoming commits
3. `git diff --stat main...upstream/main` — affected files
4. For every file changed both locally and upstream: `git diff main...upstream/main -- <file>` — read both sides before deciding
5. Stash any unstaged changes: `git stash push -m "pre-merge unstaged" -- <files>`
6. `git merge upstream/main --no-ff --no-commit` — start the merge without committing
7. Resolve conflicts using the **upstream-spine** principle (see below).
8. `git add` resolved files, then `git commit` with a message summarising what upstream brought in and what local customisations were preserved.
9. `git stash pop` — restore stashed changes
10. `git push origin main` — plain push, no force flag
11. Report: commits absorbed, conflicts resolved, files affected.

### Conflict-resolution principle: upstream spine, our feats on top

Take **upstream's shape** as the spine and re-apply our features to fit *their* structure. The feature/capability is what matters (MCP OAuth, `default_text_provider` routing, `dream.interval_h=8`). The implementation shape should follow upstream's so future syncs stay small.

Concretely:
- When auto-merge succeeds on a file we've customised, **still read the result** to verify our customisations integrated with upstream's intent rather than just survived textually.
- When a conflict marker appears, read upstream's surrounding code shape first, then re-apply our feature to fit *their* structure. Don't paste our old block back verbatim.
- If upstream now provides equivalent capability natively (e.g. Discord `group_policy="mention"` became the upstream default in v0.2.x), drop our parallel code — the feature lives in their shape now. Move the row from the "Local customisations" table down to the "Absorbed upstream" log.
- If upstream restructured an area we depend on, compose our extension *inside* their new shape (e.g. our `MCPConnection` class is used as the connection object inside upstream's `connect_single_server` rather than living alongside it).

### What counts as a conflict worth reading

Read the actual diff (step 4) for any file we've changed locally. Auto-merge is fine for files we haven't touched. Never use `-X ours` or `-X theirs` blindly.

### If a merge produces wrong file state

Save the correct tree first, then fix the commit:

```bash
git branch saved-merge HEAD          # checkpoint
git reset --soft HEAD~1              # undo just the commit, keep files staged
# fix files
git commit -m "..."
```

### What NOT to do

- `git rebase upstream/main` — rewrites pushed SHAs, forces all downstream machines to reset
- `git push --force` / `--force-with-lease` to `main` — forbidden unless a secret leaked or the tip is broken, and only after coordinating all machines
- `git commit --amend` on any commit reachable from `origin/main`

## Local customisations to preserve across every sync

These are intentional divergences from `HKUDS/nanobot`. Apply them on top of upstream's shape (see "Conflict-resolution principle" above). When upstream absorbs one, move it down to the "Absorbed upstream" log.

### MCP OAuth machinery (~entirely orthogonal to upstream — no shape conflict)

| Area | What we keep | Why |
|------|-------------|-----|
| `agent/tools/oauth_flow.py`, `oauth_tokens.py` | Entire files (upstream-untouched) | OAuth device-flow + dynamic client registration + token storage |
| `agent/tools/mcp.py` | `_resolve_oauth_token` function + call site at top of upstream's `connect_single_server` that injects `Authorization: Bearer …` into HTTP transport headers | Talk to OAuth 2.1 remote MCP servers. Token re-resolves on every reconnect because upstream's `_refresh_terminated_server` calls `connect_single_server` again — refresh-token rotation happens for free. |
| `agent/loop.py` | `_start_oauth_refresh_task` + `_oauth_refresh_loop` | Refresh OAuth tokens before expiry |
| `cli/commands.py` | `mcp-auth` Typer command (~230 lines at end of file) + `_mcp_discover_and_register` / `_mcp_auth_device_code` / `_mcp_auth_client_credentials` helpers | Interactive OAuth setup for remote MCP servers |
| `config/schema.py` | `OAuthConfig` class + `MCPServerConfig.auth` field | Wire OAuth into MCP server config |

### Multi-provider routing (clean composition inside upstream's factory/loop)

| Area | What we keep | Why |
|------|-------------|-----|
| `providers/factory.py` | Forced-provider branch at start of `_make_provider_core` + extracted `api_base` resolver + `default_text_provider` in `provider_signature` cache key | Route all text traffic through a single aggregator (e.g. OpenRouter) regardless of preset routing |
| `agent/loop.py` | `from_config`: `defaults.default_text_model or resolved.model` override; `_subagent_provider_factory` + `available_providers` / `available_models` properties | Honor `defaultTextModel` config field; per-subagent provider/model override discovery |
| `agent/subagent.py` | `provider_factory` callback + per-spawn `provider` / `model` overrides + `available_providers` / `available_models` lists | Per-subagent provider/model override |
| `agent/tools/spawn.py` | `provider` + `model` schema params | Surface the override to the agent |
| `config/schema.py` | `default_text_model`, `default_text_provider`, `default_vision_model`, `default_vision_provider` on `AgentDefaults` | Multi-provider routing fields |

### Channel hardening (pure additions / fixes upstream lacks)

| Area | What we keep | Why |
|------|-------------|-----|
| `channels/discord.py` | `@field_validator` to coerce int IDs to strings in `allow_from`; bot-source @ mention requirement in `_handle_discord_message` (orthogonal to upstream's user-side `group_policy="mention"`); extracted `_message_mentions_current_bot` helper | Other-bot messages must @ mention us to be heard; tolerate JSON-numeric Discord IDs |
| `channels/email.py` | SMTP port-vs-encryption auto-correction (587 → STARTTLS, 465 → implicit SSL) with warning logs | Common misconfiguration — silently broken otherwise |
| `agent/tools/web.py` | `WebSearchTool.exclusive = True` + `_dispatch_search` 30s timeout wrap; `WebToolsConfig.enable` `AliasChoices("enable", "enabled")` | Avoid web-search provider concurrency stalls; back-compat with older `enabled:` configs |

### Operational defaults & infra

| Area | What we keep | Why |
|------|-------------|-----|
| `config/loader.py` | Open config with `encoding="utf-8-sig"` | Tolerate UTF-8 BOM (Windows Notepad adds it) |
| `cli/onboard.py` | `importlib.import_module(channel_cls.__module__)` instead of hardcoded `nanobot.channels.{name}` | Required to resolve channel configs for **plugin** channels (live in `nanobot_channel_*` packages) |
| `pyproject.toml` | `fastapi`, `uvicorn`, `python-multipart` deps | Plugin channels (`nanobot-channel-{webui,openaiapi,mcpserver}`) require them |
| `Dockerfile` / `docker-compose.yml` | Plugin channel install + ports 18791–93 + `NANOBOT_SKIP_WEBUI_BUILD=1` env | Plugin-channel deployment shape; plugin ships its own dist, so upstream's hatch webui build is wasted work |
| `skills/memory/SKILL.md` | "When to Update MEMORY.md" + "Auto-consolidation" sections | Stronger guidance against `write_file`-ing MEMORY.md (which would destroy existing memory) |

### Absorbed upstream (no longer local)

- **Discord `group_policy="mention"` default** — used to be our fork's hard requirement; became the upstream default in v0.2.x. Nothing to preserve.
- **`dream.interval_h=8` default** — dropped after upstream's Dream refactor (`d1a94dae` replaced two-phase Dream with simple cron + `process_direct`). Runtime config also reverted to upstream's 2h default; `maxBatchSize` and `maxIterations` are now deprecated fields.
- **`MCPConnection` class + epoch-based reconnect** — dropped after upstream's `e9145b7a` / `d0eba7cd` added `_MCPWrapperBase` with `_refresh_session_after_termination` + `_attach_reconnect_handlers` + state-level `_reload_lock`. Upstream's coarser state-lock equivalently protects against thundering herd, and on session-terminated errors it tears down + rebuilds the whole server, swapping the live session into each wrapper. Only the OAuth token resolution remained local (now injected at the top of upstream's `connect_single_server`). `tests/agent/test_mcp_reconnect.py` deleted (its subject no longer exists).
- **`_mcp_owner` task + `_mcp_shutdown_event`** — dropped after upstream's `edf78e70` added `_OwnedMCPConnection` + a per-server `mcp:{name}` owner task inside `connect_single_server`. Our single loop-level owner task existed to keep anyio cancel scopes off `run()`; upstream now does the same thing per-connection (each task opens its stack, waits on `close_requested`, closes in its own `finally`), which is strictly finer-grained — one failing server can't disturb the others, and reconnect/hot-reload close independently. `run()` now just `await self._connect_mcp()` and `close_mcp()` just delegates to `agent_context.close_mcp`. `_start_oauth_refresh_task` / `_oauth_refresh_loop` remain local.

## Local testing

Always test with `uv run pytest tests/`. Discord tests are skipped unless `discord.py` is installed (`pip install nanobot-ai[discord]`).

### Prefer host runs; the container is a last resort, not the default

Verify on the Windows host. Run the suites that touch your change plus a broad
sweep (`uv run pytest tests/agent tests/providers tests/tools tests/config`) —
these complete fine on the host. **Then read every failure.** Windows-only
failures are recognizable and expected: PowerShell writes UTF-16-BOM instead of
POSIX output (upstream defaults Windows `exec` to PowerShell), and git autocrlf
produces phantom diffs. If the failing tests are upstream's new tests and the
cause is a Windows shell/git artifact, they cannot fail on Linux for a
merge-related reason — a container run proves nothing new. Don't do it.

**The narrow case for the container**: only the *whole-tree* `uv run pytest
tests/` run hangs on the Windows host — pytest wedges on async teardown after
certain tests (e.g. `tests/cli/test_restart_command.py::test_status_intercepted_in_run_loop`)
because Windows' ProactorEventLoop leaks dangling tasks; the tests themselves
PASS. Subdirectory runs are fine. So reach for the container ONLY when you
genuinely need a single whole-tree pass AND a failure's cause is truly
ambiguous — not as routine post-merge verification.

Note `tests/` is **not** in the image (the Dockerfile copies only `nanobot/` +
`plugins/`), so a container test run needs a repo bind-mount, `uv run --with
pytest ...`, and non-root workarounds. That friction is the tell that you've
left the fast path — usually the host evidence already answers the question.
