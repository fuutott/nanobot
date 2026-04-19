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
7. Resolve conflicts: **prefer local by default**; accept upstream only when it is clearly better or makes local redundant. For Discord bot filtering, always keep the @ mention requirement.
8. `git add` resolved files, then `git commit` with a message summarising what upstream brought in and what local customisations were preserved.
9. `git stash pop` — restore stashed changes
10. `git push origin main` — plain push, no force flag
11. Report: commits absorbed, conflicts resolved, files affected.

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

These are intentional divergences from `HKUDS/nanobot`. Keep them on conflict:

| Area | What we keep | Why |
|------|-------------|-----|
| `channels/discord.py` | Require @ mention from other bots (not just self-loop guard) | Don't want bots triggering the bot without an explicit mention |
| `config/schema.py` | `default_text_model`, `default_text_provider`, vision variants | Multi-provider routing |
| `config/schema.py` | `dream.interval_h` default = 8h | Less frequent than upstream's 2h |
| `config/schema.py` | `web.enable` alias via `AliasChoices` | Config compatibility |
| `cli/commands.py` | `defaultTextProvider` routing in `_make_provider`; `sender_id` threading | Provider selection UX |
| `pyproject.toml` | `fastapi`, `uvicorn`, `python-multipart` deps | Plugin channels require them |

## Local testing

Always test with `uv run pytest tests/`. Discord tests are skipped unless `discord.py` is installed (`pip install nanobot-ai[discord]`).
