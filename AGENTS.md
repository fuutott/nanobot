# Project Notes

## Git Sync Preferences

When syncing with upstream (e.g. `git merge upstream/main`), always prefer local changes unless upstream explicitly makes them better or removes the need for them. On conflict, keep ours by default and evaluate upstream's version on its merits. If uncertain ask.

**Before merging**, always review what upstream is bringing in:
1. Run `git log --oneline main...upstream/main` to see incoming commits.
2. Run `git diff --stat main...upstream/main` to see affected files.
3. Check for overlaps with local changes using `git diff main...upstream/main -- <files that also differ locally>`.
4. If any file has both local and upstream changes, read the actual diff for that file before merging. Evaluate upstream's version on its merits — adopt it only if it's clearly better or makes local changes redundant.
5. Do NOT blindly merge with `-X ours`. Only use conflict resolution strategies after understanding the actual changes.

## Local testing

Always test with uv.
