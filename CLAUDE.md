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
7. Resolve conflicts: **prefer local by default**; accept upstream only when it is clearly better or makes local redundant. For Discord bot filtering, always keep the @ mention requirement.
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

See [AGENTS.md](AGENTS.md) for the authoritative table. At time of writing: Discord @ mention requirement, `default_text_provider`/vision routing in `config/schema.py`, `dream.interval_h=8` default, `web.enable` `AliasChoices` alias, `defaultTextProvider` routing in `cli/commands.py`, plugin-channel deps (`fastapi`, `uvicorn`, `python-multipart`) in `pyproject.toml`.

## Upstream AI-contributor docs

Upstream now ships [`.agent/design.md`](.agent/design.md), [`.agent/security.md`](.agent/security.md), and [`.agent/gotchas.md`](.agent/gotchas.md) — read these for architecture/security/gotcha context that is not in this file.
