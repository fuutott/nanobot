# Project Notes

## Git Sync Preferences

Always prefer local changes over upstream unless upstream is clearly better or makes local changes redundant. On conflict, keep local by default and evaluate upstream on its merits. Never blindly merge. Use rebase, not merge.

**Before rebasing**, always review what upstream is bringing in:
1. `git fetch upstream`
2. `git log --oneline main..upstream/main` — incoming commits
3. `git diff --stat main...upstream/main` — affected files
4. `git diff main...upstream/main -- <overlapping files>` — read actual diffs for any file changed both locally and upstream
5. Do NOT use `-X ours` without understanding the changes.

**After a clean rebase:** `git push origin main --force-with-lease`

### Trigger phrase

When the user posts a message matching:

> "This branch is N commits ahead of and M commits behind HKUDS/nanobot:main."

Run the full sync automatically (fetch → review → stash unstaged → rebase → pop stash → force-push origin) without asking for confirmation.

## Local testing

Always test with uv.
