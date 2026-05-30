# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Nanobot** is an ultra-lightweight personal AI agent framework (Python 3.11+). This is a personal fork of `HKUDS/nanobot` that adds plugin channels (Web UI, OpenAI API, MCP Server), tighter Docker defaults with pre-installed plugins, MCP OAuth 2.1 support for remote MCP servers, and dev workflow scripts for WIP syncing.

## Commands

```bash
# Install for development
pip install -e ".[dev]"
# or with uv (preferred):
uv sync --all-extras

# Run tests
uv run pytest tests/

# Run a single test
uv run pytest tests/path/to/test_file.py::test_name

# Lint (unused imports/variables only — as configured in CI)
uv run ruff check nanobot --select F401,F841

# Full lint (all configured rules: E, F, I, N, W — ignores E501)
uv run ruff check nanobot

# Format
uv run ruff format nanobot/

# Run nanobot CLI
uv run nanobot
```

Always test with `uv` (not plain `python` or `pytest`).

## Architecture

Nanobot uses a **message-driven, async-first** architecture. All components use `asyncio`; pytest is configured with `asyncio_mode = "auto"`.

### Message Flow

Messages flow through an async `MessageBus` (`nanobot/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`nanobot/channels/`) receive messages from external platforms and publish `InboundMessage` events to the bus.
2. **`AgentLoop`** (`nanobot/agent/loop.py`) consumes inbound messages, builds context, and coordinates the turn.
3. **`AgentRunner`** (`nanobot/agent/runner.py`) handles the actual LLM conversation loop: send messages to the provider, receive tool calls, execute tools, and stream responses.
4. Responses are published as `OutboundMessage` events back to the appropriate channel.

### Key Subsystems

- **Agent Loop** (`nanobot/agent/loop.py`, `runner.py`): Core processing engine. `AgentLoop` manages session keys, hooks, and context building; `AgentRunner` executes the multi-turn LLM conversation with tool execution. Includes model fallback (`providers/fallback_provider.py`) and runtime model preset switching (`agent/model_presets.py`).
- **LLM Providers** (`nanobot/providers/`): Provider implementations (Anthropic, OpenAI-compatible, OpenAI Responses API, Azure, Bedrock, GitHub Copilot, OpenAI Codex, NVIDIA NIM, Atomic Chat, and ~20 others). Image generation (`image_generation.py`) and audio transcription (`transcription.py`) live here too. `factory.py` and `registry.py` handle instantiation and model discovery.
- **Channels** (`nanobot/channels/`): Platform integrations (CLI, Telegram, Slack, Discord, Feishu, Matrix, WhatsApp, QQ, WeChat, WeCom, DingTalk, Email, MoChat, MS Teams, WebSocket). `manager.py` discovers and coordinates them. Auto-discovered via `pkgutil` scan + entry-point plugins.
- **Tools** (`nanobot/agent/tools/`): Agent capabilities: filesystem (read/write/edit/list/grep), shell execution (with sandbox backends), web search/fetch, MCP servers (with OAuth 2.1 for remote servers, local fork addition), cron, notebook editing, subagent spawning, long-running tasks / sustained goals (`long_task.py`), image generation, self-modification. Auto-discovered via plugin loader (`agent/tools/loader.py`).
- **Memory** (`nanobot/agent/memory.py`): Session history persistence with Dream two-phase consolidation. Atomic writes with fsync for durability.
- **Session Management** (`nanobot/session/`): Per-session history, context compaction, TTL-based auto-compaction (`manager.py`), and sustained-goal state tracking (`goal_state.py`).
- **Config** (`nanobot/config/schema.py`, `loader.py`): Pydantic-based, loaded from `~/.nanobot/config.json`. Supports camelCase aliases for JSON compatibility. Model presets enable atomic provider/model switching at runtime.
- **Bridge** (`bridge/`): TypeScript services (WhatsApp bridge) bundled into the wheel via `pyproject.toml` `force-include`.
- **WebUI** (`webui/`): Vite-based React SPA that talks to the gateway over a WebSocket multiplex protocol. Upstream now bundles its dist into the wheel via `hatch_build.py`; this fork additionally ships it as the `nanobot-channel-webui` plugin (see Plugin Channels below).
- **API Server** (`nanobot/api/server.py`): OpenAI-compatible HTTP API (`/v1/chat/completions`, `/v1/models`).
- **Command Router** (`nanobot/command/`): Slash command routing and built-in command handlers.
- **Cron** (`nanobot/cron/`): Persistent scheduler for periodic agent tasks.
- **Heartbeat** (`nanobot/templates/HEARTBEAT.md`): Periodic task list checked via `cron` jobs (legacy dedicated service removed in v0.2.x).
- **Pairing** (`nanobot/pairing/`): DM sender approval store with persistent pairing codes per channel. When `allowFrom` is omitted, channels default to pairing-only mode.
- **Skills** (`nanobot/skills/`): Markdown-based teachable behaviours with YAML frontmatter (`skills/*/SKILL.md`). Built-ins: long-goal, cron, GitHub, image-generation, memory/summarization, skill-creator, etc.
- **Security** (`nanobot/security/`): Workspace sandboxing, SSRF guard (with IPv6-mapped IPv4 normalization), PTH file guard.
- **CLI** (`nanobot/cli/`): Typer-based entry point (`commands.py`), interactive REPL via `prompt_toolkit`, setup wizard (`onboard.py`), streaming output (`stream.py`).

### Plugin Channels (`plugins/`)

Three optional packages installable as Python packages (fork-specific):
- `nanobot-channel-webui` — Browser UI (ships with prebuilt webui dist)
- `nanobot-channel-openaiapi` — HTTP API endpoint
- `nanobot-channel-mcpserver` — MCP server integration

These wrap upstream's in-tree implementations as separately installable channels so the Docker image can compose them cleanly.

### WhatsApp Bridge (`bridge/`)

Node.js service using Baileys/Whiskey Sockets. Bundled into the wheel via `pyproject.toml` force-include.

### Docker

- Base image: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` with Node.js 20, git, curl, bubblewrap
- Runs as non-root user `nanobottie`
- Exposed ports: 18790 (gateway), 18791–18793 (plugins)
- Resource limits: 1 CPU / 1 GB memory

## Project-Specific Notes (upstream `.agent/` docs)

Upstream ships dedicated AI-contributor guides — read these for architecture constraints, security boundaries, and gotchas not covered above:

- [.agent/design.md](.agent/design.md)
- [.agent/security.md](.agent/security.md)
- [.agent/gotchas.md](.agent/gotchas.md)

## Documentation (`docs/`)

Reference docs for subsystems:

- [docs/MEMORY.md](docs/MEMORY.md) — Memory architecture: `Consolidator` (summarises old turns → `memory/history.jsonl`) and `Dream` (cron job that merges history into `SOUL.md`, `USER.md`, `memory/MEMORY.md`). Covers `/dream` commands, `GitStore` versioning, and `agents.defaults.dream` config fields (`intervalH`, `modelOverride`, `maxBatchSize`, `maxIterations`).
- [docs/PYTHON_SDK.md](docs/PYTHON_SDK.md) — Programmatic API (`Nanobot.from_config()`, `bot.run()`, `RunResult`). Covers `AgentHook` lifecycle callbacks and `finalize_content` pipeline.
- [docs/WEBSOCKET.md](docs/WEBSOCKET.md) — WebSocket server channel (`channels.websocket`): wire protocol (`ready`/`message`/`delta`/`stream_end` events), token issuance, TLS, and `allowFrom` access control.
- [docs/CHANNEL_PLUGIN_GUIDE.md](docs/CHANNEL_PLUGIN_GUIDE.md) — How to build and package a custom channel plugin: subclass `BaseChannel`, register under the `nanobot.channels` entry point group, use a Pydantic config model (required — plain `dict` breaks `is_allowed()`), and optionally implement `send_delta()` for streaming.
- Upstream also ships [docs/configuration.md](docs/configuration.md), [docs/chat-commands.md](docs/chat-commands.md), and [docs/channel-plugin-guide.md](docs/channel-plugin-guide.md).

## Common File Locations

- Config schema: `nanobot/config/schema.py`
- Provider base / new provider template: `nanobot/providers/base.py`
- Channel base / new channel template: `nanobot/channels/base.py`
- Tool registry: `nanobot/agent/tools/registry.py` (plugin loader: `nanobot/agent/tools/loader.py`)
- WebUI dev proxy config: `webui/vite.config.ts`
- Tests mirror the `nanobot/` package structure.

## Upstream Sync Protocol

This is a fork synced across multiple machines via plain `git pull` from `origin`. The cardinal rule: **never rewrite commits already on `origin/main`**. The canonical, machine-readable protocol lives in [AGENTS.md](AGENTS.md) — keep both files in sync.

### Sync strategy: merge, not rebase

We keep local customisations on top of `HKUDS/nanobot`. The right tool for syncing is **`git merge upstream/main`**, not `git rebase upstream/main`.

Why merge, not rebase:
- Rebase rewrites local SHAs → requires `--force-with-lease` → breaks every other machine's `git pull`.
- Merge creates a new commit with `upstream/main` as a parent → git forever knows we have those upstream commits → `git log main..upstream/main` only shows truly new work → clean future syncs.
- Fast-forward push to `origin/main` → downstream machines just `git pull`.

### Trigger phrase

When the user posts a message matching the pattern:

> "This branch is N commits ahead of and M commits behind HKUDS/nanobot:main."

**Automatically execute the full sync without asking:**

1. `git fetch upstream` — get latest upstream commits
2. `git log --oneline main..upstream/main` — review incoming commits
3. `git diff --stat main...upstream/main` — affected files
4. For every file changed both locally and upstream: `git diff main...upstream/main -- <file>` — read both sides before deciding
5. Stash any unstaged changes: `git stash push -m "pre-merge unstaged" -- <files>`
6. `git merge upstream/main --no-ff --no-commit` — start the merge without committing
7. Resolve conflicts using the **upstream-spine** principle: adopt upstream's shape and re-apply our features to fit *their* structure. Don't preserve parallel/legacy implementations. See [AGENTS.md](AGENTS.md) § "Conflict-resolution principle" for the full rule.
8. `git add` resolved files, then `git commit` with a message summarising what upstream brought in and what local customisations were preserved.
9. `git stash pop` — restore stashed changes
10. `git push origin main` — plain push, no force flag
11. Report: commits absorbed, conflicts resolved, files affected.

### What counts as a conflict worth reading

Read the actual diff (step 4) for any file we've changed locally. Auto-merge is fine for files we haven't touched. Never use `-X ours` or `-X theirs` blindly.

### What NOT to do

- `git rebase upstream/main` — rewrites pushed SHAs, forces all downstream machines to reset
- `git push --force` / `--force-with-lease` to `main` — forbidden unless a secret leaked or the tip is broken, and only after coordinating all machines
- `git commit --amend` on any commit reachable from `origin/main`

### Local customisations to preserve across every sync

See [AGENTS.md](AGENTS.md) for the authoritative table. At time of writing: Discord @ mention requirement, `default_text_provider`/vision routing in `config/schema.py`, `dream.interval_h=8` default, `web.enable` `AliasChoices` alias, `defaultTextProvider` routing in `cli/commands.py` (via patched `AgentLoop.from_config`), plugin-channel deps (`fastapi`, `uvicorn`, `python-multipart`) in `pyproject.toml`, MCP OAuth 2.1 client (`agent/tools/oauth_flow.py`, `oauth_tokens.py`, `MCPConnection` class in `agent/tools/mcp.py`, OAuth refresh loop in `agent/loop.py`).
