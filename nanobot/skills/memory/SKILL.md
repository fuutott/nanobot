---
name: memory
description: Search conversation history and understand Dream-managed profile and memory files.
---

# Memory

## Structure

- `SOUL.md` — Bot personality and communication style. **Managed by Dream.** Do NOT edit.
- `USER.md` — User profile and preferences. **Managed by Dream.** Do NOT edit.
- `memory/MEMORY.md` — Long-term facts (project context, important events). **Managed by Dream.** Do NOT edit.
- `memory/history.jsonl` — append-only JSONL, not loaded into context. Prefer the
  built-in `grep` tool to search it.

## Search Past Events

Use the `History log` path shown in the system prompt. Always pass it to `grep`;
never substitute a different project-relative `memory/history.jsonl`, which may belong
to the selected project. Each JSONL line contains `cursor`, `timestamp`, and `content`.

- For broad searches, start with `output_mode="count"` or the default
  `files_with_matches` mode before expanding to full content
- Use `output_mode="content"` plus `context_before` / `context_after` when you need the exact matching lines
- Use `fixed_strings=true` for literal timestamps or JSON fragments
- Use `head_limit` / `offset` to page through long histories

Examples (replace `<history-log-path>` with the path from the system prompt):
- `grep(pattern="keyword", path="<history-log-path>", case_insensitive=true)`
- `grep(pattern="2026-04-02 10:00", path="<history-log-path>", fixed_strings=true)`
- `grep(pattern="keyword", path="<history-log-path>", output_mode="count", case_insensitive=true)`
- `grep(pattern="oauth|token", path="<history-log-path>", output_mode="content", case_insensitive=true)`

## Important

- **Do NOT edit SOUL.md, USER.md, or MEMORY.md.** They are automatically managed by Dream.
- If you notice outdated information, it will be corrected when Dream runs next.
- Users can view Dream's activity with the `/dream-log` command.

## When to Update MEMORY.md

Write important facts immediately using `edit_file` to **add or update specific sections** — never use `write_file` on MEMORY.md, as that would destroy all existing memory:
- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")

**MEMORY.md is cumulative.** Always preserve all existing content. Only append new facts or edit specific lines — never replace the whole file.

## Auto-consolidation

Old conversations are automatically summarized and appended to HISTORY.md when the session grows large. Long-term facts are extracted to MEMORY.md. You don't need to manage this.
