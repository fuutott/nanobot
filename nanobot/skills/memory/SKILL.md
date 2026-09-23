---
name: memory
description: Search past conversations in the agent's history log.
---

# Memory

## Search Past Events

Search the exact `History log` path from the system prompt with `grep`; a project-relative
`memory/history.jsonl` may belong to a different workspace. The log is append-only JSONL,
with `cursor`, `timestamp`, and `content` per entry, and is not loaded into context.

Start broad searches with `output_mode="count"`, then narrow by topic or date and request
matching content. Use `fixed_strings=true` for literal timestamps or JSON fragments.
Page long results with `head_limit` / `offset` and use `context_before` / `context_after`
when nearby entries matter.

Example (replace `<history-log-path>` with the path from the system prompt):
`grep(pattern="project-name", path="<history-log-path>", output_mode="content", case_insensitive=true, head_limit=20)`

## When to Update MEMORY.md

Write important facts immediately using `edit_file` to **add or update specific sections** — never use `write_file` on MEMORY.md, as that would destroy all existing memory:
- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")

**MEMORY.md is cumulative.** Always preserve all existing content. Only append new facts or edit specific lines — never replace the whole file.

## Auto-consolidation

Older conversation is archived to the history log automatically, and Dream periodically folds durable facts into MEMORY.md. You don't need to manage this.
