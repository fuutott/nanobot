# Project Notes

This is my primary edit machine. Work committed here is pushed to `origin` (`fuutott/nanobot`) and pulled on other machines. Other machines do a plain `git pull` — no force, no reset. The cardinal rule: **never rewrite commits already on `origin/main`**.

## Sync strategy: merge, not rebase

We keep local customisations on top of `HKUDS/nanobot`. The right tool for syncing is **`git merge upstream/main`**, not `git rebase upstream/main`.

Why merge, not rebase:
- Rebase rewrites local SHAs → requires `--force-with-lease` → breaks every other machine's `git pull`.
- Merge creates a new commit with `upstream/main` as a parent → git forever knows we have those upstream commits → `git log main..upstream/main` only shows truly new work → clean future syncs.
- Fast-forward push to `origin/main` → downstream machines just `git pull`.

## Upstream sync protocol

### Trigger phrase

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

Take **upstream's shape** as the spine and re-apply our features to fit *their* structure. The feature/capability is what matters (Discord @ mention, MCP OAuth, `default_text_provider` routing, `dream.interval_h=8`). The implementation shape should follow upstream's so future syncs stay small.

Concretely:
- When auto-merge succeeds on a file we've customised, **still read the result** to verify our customisations integrated with upstream's intent rather than just survived textually.
- When a conflict marker appears, read upstream's surrounding code shape first, then re-apply our feature to fit *their* structure. Don't paste our old block back verbatim.
- If upstream now provides equivalent capability natively (e.g. Discord `group_policy="mention"` became the upstream default in v0.2.x), drop our parallel code — the feature lives in their shape now. Move the row from the "Local customisations" table down to the changelog.
- If upstream restructured an area we depend on, compose our extension *inside* their new shape (e.g. our `MCPConnection` class is now used as the connection object inside upstream's `connect_single_server` rather than living alongside it).

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

| Area | What we keep | Why |
|------|-------------|-----|
**MCP OAuth machinery** (~entirely orthogonal to upstream — no shape conflict):
| Area | What we keep | Why |
|------|-------------|-----|
| `agent/tools/oauth_flow.py`, `oauth_tokens.py` | Entire files (upstream-untouched) | OAuth device-flow + dynamic client registration + token storage |
| `agent/tools/mcp.py` | `MCPConnection` class (composed inside upstream's `connect_single_server`) + `_resolve_oauth_token` + epoch-based reconnect on transient errors | Talk to OAuth 2.1 remote MCP servers; rebuild stale anyio streams without thundering-herd |
| `agent/loop.py` | `_mcp_owner` task wrapping `_connect_mcp()` + `_start_oauth_refresh_task` + `_oauth_refresh_loop` | Isolate anyio cancel scopes from `run()`; refresh OAuth tokens before expiry |
| `cli/commands.py` | `mcp-auth` Typer command (~230 lines at end of file) + `_mcp_discover_and_register` / `_mcp_auth_device_code` / `_mcp_auth_client_credentials` helpers | Interactive OAuth setup for remote MCP servers |
| `config/schema.py` | `OAuthConfig` class + `MCPServerConfig.auth` field | Wire OAuth into MCP server config |

**Multi-provider routing** (clean composition inside upstream's factory/loop):
| Area | What we keep | Why |
|------|-------------|-----|
| `providers/factory.py` | Forced-provider branch at start of `_make_provider_core` + extracted `api_base` resolver + `default_text_provider` in `provider_signature` cache key | Route all text traffic through a single aggregator (e.g. OpenRouter) regardless of preset routing |
| `agent/loop.py` | `from_config`: `defaults.default_text_model or resolved.model` override | Honor `defaultTextModel` config field |
| `config/schema.py` | `default_text_model`, `default_text_provider`, `default_vision_model`, `default_vision_provider` on `AgentDefaults` | Multi-provider routing fields |

**Channel hardening** (pure additions / fixes upstream lacks):
| Area | What we keep | Why |
|------|-------------|-----|
| `channels/discord.py` | `@field_validator` to coerce int IDs to strings in `allow_from`; bot-source @ mention requirement in `_handle_discord_message` (orthogonal to upstream's user-side `group_policy="mention"`); extracted `_message_mentions_current_bot` helper | Other-bot messages must @ mention us to be heard; tolerate JSON-numeric Discord IDs |
| `channels/email.py` | SMTP port-vs-encryption auto-correction (587 → STARTTLS, 465 → implicit SSL) with warning logs | Common misconfiguration — silently broken otherwise |
| `agent/tools/web.py` | `WebSearchTool.exclusive = True` + `_dispatch_search` 30s timeout wrap; `WebToolsConfig.enable` `AliasChoices("enable", "enabled")` | Avoid web-search provider concurrency stalls; back-compat with older `enabled:` configs |

**Operational defaults & infra**:
| Area | What we keep | Why |
|------|-------------|-----|
| `config/schema.py` | `dream.interval_h` default = 8h (kept upstream's `dream.enabled` toggle) | Personal-agent tempo; less frequent than upstream's 2h |
| `config/loader.py` | Open config with `encoding="utf-8-sig"` | Tolerate UTF-8 BOM (Windows Notepad adds it) |
| `cli/onboard.py` | `importlib.import_module(channel_cls.__module__)` instead of hardcoded `nanobot.channels.{name}` | Required to resolve channel configs for **plugin** channels (live in `nanobot_channel_*` packages) |
| `pyproject.toml` | `fastapi`, `uvicorn`, `python-multipart` deps | Plugin channels (`nanobot-channel-{webui,openaiapi,mcpserver}`) require them |
| `Dockerfile` / `docker-compose.yml` | Plugin channel install + ports 18791–93 + `NANOBOT_SKIP_WEBUI_BUILD=1` env | Plugin-channel deployment shape; plugin ships its own dist, so upstream's hatch webui build is wasted work |
| `skills/memory/SKILL.md` | "When to Update MEMORY.md" + "Auto-consolidation" sections | Stronger guidance against `write_file`-ing MEMORY.md (which would destroy existing memory) |

### Absorbed upstream (no longer local)

- **Discord `group_policy="mention"` default** — used to be our fork's hard requirement; became the upstream default in v0.2.x. Nothing to preserve.

## Local testing

Always test with `uv run pytest tests/`. Discord tests are skipped unless `discord.py` is installed (`pip install nanobot-ai[discord]`).
