---
name: memory
description: Search past conversations in the agent's history log.
---

# Memory

## Search Past Events

Search the exact `History log` path from the system prompt using an available text-search
tool. This path belongs to the agent workspace, which can differ from the current project.

The append-only JSONL log stores `cursor`, `timestamp`, and `content` per entry. Retrieve
entries on demand by topic or date, and inspect neighboring entries when context matters.

## When to Update MEMORY.md

Write important facts immediately using `edit_file` to **add or update specific sections** — never use `write_file` on MEMORY.md, as that would destroy all existing memory:
- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")

**MEMORY.md is cumulative.** Always preserve all existing content. Only append new facts or edit specific lines — never replace the whole file.

## Auto-consolidation

Older conversation is archived to the history log automatically, and Dream periodically folds durable facts into MEMORY.md. You don't need to manage this.
