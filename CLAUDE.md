# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Nanobot** is an ultra-lightweight personal AI agent framework (Python 3.11+). This is a personal fork of `HKUDS/nanobot` that adds plugin channels (Web UI, OpenAI API, MCP Server), tighter Docker defaults with pre-installed plugins, and dev workflow scripts for WIP syncing.

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

```
Channel (inbound) → Bus → Agent Loop → Tools → Response → Channel (outbound)
```

### Key Modules

- **`nanobot/agent/`** — Core agent logic: `loop.py` (LLM + tool orchestration), `runner.py` (multi-turn execution), `memory.py` (persistent history + dream consolidation for long-term memory)
- **`nanobot/channels/`** — Platform integrations (CLI, Telegram, Slack, Discord, WeChat, Feishu, DingTalk, QQ, Email, Matrix, and more). Each channel connects to the Bus.
- **`nanobot/providers/`** — LLM backend adapters: Anthropic, OpenAI, Azure, GitHub Copilot, Codex, and 20+ others via a registry. Interface-based; not SDK-specific.
- **`nanobot/agent/tools/`** — Agent capabilities: filesystem, shell, web search/fetch, glob/grep, cron, MCP client, notebook editing, inter-agent messaging, background task spawning.
- **`nanobot/skills/`** — Teachable behaviors stored as Markdown files with YAML frontmatter in `skills/*/SKILL.md`. Built-ins: GitHub, weather, cron, memory/summarization, ClawHub discovery.
- **`nanobot/bus/`** — Event-driven message routing between all components.
- **`nanobot/session/`** — Session manager: per-session conversation context and history isolation.
- **`nanobot/config/`** — Pydantic schema-based config, loaded from `~/.nanobot/config.json` with env var support.
- **`nanobot/api/server.py`** — OpenAI-compatible HTTP API (`/v1/chat/completions`) via FastAPI + uvicorn.
- **`nanobot/cron/`** — Task scheduling and heartbeat.
- **`nanobot/security/`** — Workspace sandboxing; tools are restricted to a configurable workspace directory.
- **`nanobot/cli/`** — Typer-based CLI entry point (`commands.py`), interactive REPL (`prompt_toolkit`), setup wizard (`onboard.py`), streaming output (`stream.py`).

### Plugin Channels (`plugins/`)

Three optional packages installable as Python packages:
- `nanobot-channel-webui` — Browser UI
- `nanobot-channel-openaiapi` — HTTP API endpoint
- `nanobot-channel-mcpserver` — MCP server integration

### WhatsApp Bridge (`bridge/`)

Node.js service using Baileys/Whiskey Sockets. Bundled into the wheel via `pyproject.toml` force-include.

### Docker

- Base image: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` with Node.js 20, git, curl, bubblewrap
- Runs as non-root user `nanobottie`
- Exposed ports: 18790 (gateway), 18791–18793 (plugins)
- Resource limits: 1 CPU / 1 GB memory

## Documentation (`docs/`)

Reference docs for subsystems not covered above:

- [docs/MEMORY.md](docs/MEMORY.md) — Memory architecture: `Consolidator` (summarises old turns → `memory/history.jsonl`) and `Dream` (cron job that merges history into `SOUL.md`, `USER.md`, `memory/MEMORY.md`). Covers `/dream` commands, `GitStore` versioning, and `agents.defaults.dream` config fields (`intervalH`, `modelOverride`, `maxBatchSize`, `maxIterations`). Planned for v0.1.5.
- [docs/PYTHON_SDK.md](docs/PYTHON_SDK.md) — Programmatic API (`Nanobot.from_config()`, `bot.run()`, `RunResult`). Covers `AgentHook` lifecycle callbacks and `finalize_content` pipeline. Planned for v0.1.5.
- [docs/WEBSOCKET.md](docs/WEBSOCKET.md) — WebSocket server channel (`channels.websocket`): wire protocol (`ready`/`message`/`delta`/`stream_end` events), token issuance, TLS, and `allowFrom` access control.
- [docs/CHANNEL_PLUGIN_GUIDE.md](docs/CHANNEL_PLUGIN_GUIDE.md) — How to build and package a custom channel plugin: subclass `BaseChannel`, register under the `nanobot.channels` entry point group, use a Pydantic config model (required — plain `dict` breaks `is_allowed()`), and optionally implement `send_delta()` for streaming.

## Upstream Sync Protocol

When syncing with upstream (`HKUDS/nanobot`), **always prefer local changes** unless upstream is clearly better. Never blindly merge.

1. `git log --oneline main...upstream/main` — review incoming commits
2. `git diff --stat main...upstream/main` — see affected files
3. For files changed both locally and upstream, read the actual diff before deciding
4. Use rebase (not merge). Do NOT use `-X ours` without understanding the changes.

### Trigger phrase

When the user posts a message matching the pattern:

> "This branch is N commits ahead of and M commits behind HKUDS/nanobot:main."

**Automatically execute the full sync without asking:**

1. `git fetch upstream` — get latest upstream commits
2. `git log --oneline main..upstream/main` — show what's incoming
3. `git diff --stat main...upstream/main` — show affected files
4. For any file changed both locally and upstream, read the actual diffs
5. Stash any unstaged changes (`git stash push -m "pre-rebase unstaged changes" -- <files>`)
6. `git rebase upstream/main` — rebase; resolve conflicts preferring local unless upstream is clearly better
7. `git stash pop` — restore stashed changes
8. `git push origin main --force-with-lease` — sync the fork
9. Report what happened (commits absorbed, any conflicts resolved, files affected)
