#!/usr/bin/env bash
# checkpoint.sh — amend the single rolling WIP commit on origin/$WIP_BRANCH.
# The WIP branch always stays exactly 1 commit ahead of main — force-pushed every time.
# Use promote.sh to squash WIP into a clean commit on main.
#
# Usage:
#   ./checkpoint.sh
#   ./checkpoint.sh "before refactor"
#   WIP_BRANCH=wip/nanobottie/cloudvm ./checkpoint.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

MESSAGE="${1:-${CHECKPOINT_MSG:-}}"
WIP_BRANCH="${WIP_BRANCH:-wip/nanobottie/devbox}"

# ── guard: no checkpointing during merge / rebase / cherry-pick ─────────────────
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
for blocker in rebase-apply rebase-merge MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
    if [ -e "${GIT_DIR}/${blocker}" ]; then
        echo "Checkpoint skipped — ${blocker} detected. Resolve the operation first."
        exit 0
    fi
done

# ── guard: unmerged paths (conflict markers) ─────────────────────────────────────
unmerged=$(git diff --name-only --diff-filter=U 2>/dev/null)
if [ -n "$unmerged" ]; then
    echo "Checkpoint skipped — unmerged paths detected:"
    echo "$unmerged" | sed 's/^/  /'
    exit 0
fi

# ── guard: verify 'origin' remote exists ──────────────────────────────────────
if ! git remote get-url origin >/dev/null 2>&1; then
    echo "Checkpoint skipped — no 'origin' remote configured. Run: git remote add origin <url>"
    exit 0
fi

# ── stash working tree so we can safely switch branches ───────────────────────
CURRENT=$(git rev-parse --abbrev-ref HEAD)
DEV_EXISTS=$(git branch --list "$WIP_BRANCH")
stash_out=$(git stash --include-untracked 2>&1)
did_stash=false
[[ "$stash_out" != *"No local changes to save"* ]] && did_stash=true

# ── fetch + fast-forward main to origin/main ─────────────────────────────────
echo "  Syncing main with origin/main..."
git fetch origin --prune --quiet
git checkout main --quiet
if ! git pull origin main --ff-only --quiet 2>/dev/null; then
    # Can't fast-forward — local main has diverged. Restore and bail.
    $did_stash && git stash pop --quiet 2>/dev/null || true
    git checkout "$CURRENT" --quiet 2>/dev/null || true
    echo "Checkpoint aborted — local main has diverged from origin/main."
    echo "Run: git checkout main && git log --oneline -5"
    exit 1
fi

# ── ensure WIP branch exists and rebase onto updated main ────────────────────────────
if [ -z "$DEV_EXISTS" ]; then
    echo "Creating local $WIP_BRANCH from main..."
    git checkout -b "$WIP_BRANCH" main
else
    git checkout "$WIP_BRANCH" --quiet
    if ! git rebase main --quiet 2>/dev/null; then
        git rebase --abort 2>/dev/null || true
        $did_stash && git stash pop --quiet 2>/dev/null || true
        echo "Checkpoint aborted — could not rebase $WIP_BRANCH onto main."
        echo "Run: git checkout $WIP_BRANCH && git rebase main"
        exit 1
    fi
fi

# ── restore stash onto dev ─────────────────────────────────────────────────────────────────
if $did_stash; then
    git stash pop --quiet
fi

# ── stage everything ──────────────────────────────────────────────────────────
git add -A

if git diff --cached --quiet && git diff --quiet; then
    echo "Nothing to checkpoint — working tree clean."
    exit 0
fi

# ── guard: refuse to commit secrets / junk ────────────────────────────────────
bad=$(git diff --cached --name-only 2>/dev/null \
    | grep -iE '(^|/)\.env$|\.pem$|\.key$|\.p12$|\.pfx$|secret|credential|id_rsa|id_ed25519' \
    || true)
if [ -n "$bad" ]; then
    git restore --staged . 2>/dev/null || true
    echo "Checkpoint aborted — suspicious files staged:"
    echo "$bad" | sed 's/^/  /'
    echo "Add them to .gitignore if intentional, then retry."
    exit 1
fi

# ── amend if last commit is a WIP, otherwise fresh commit ─────────────────────
TS=$(date -u +"%Y-%m-%d %H:%M UTC")
LABEL=${MESSAGE:+" · $MESSAGE"}
COMMIT_MSG="wip: ${TS}${LABEL}"

LAST_MSG=$(git log -1 --pretty=%s 2>/dev/null || true)

if [[ "$LAST_MSG" == wip:* ]]; then
    git commit --amend -m "$COMMIT_MSG" --quiet
    echo "✓ Amended WIP: $COMMIT_MSG"
else
    git commit -m "$COMMIT_MSG" --quiet
    echo "✓ Committed WIP: $COMMIT_MSG"
fi

# ── force-push (amend rewrites history — that's the point) ────────────────────
if git rev-parse --abbrev-ref "${WIP_BRANCH}@{upstream}" >/dev/null 2>&1; then
    git push origin "$WIP_BRANCH" --force-with-lease --quiet
else
    git push -u origin "$WIP_BRANCH" --force-with-lease --quiet
fi
echo "✓ Pushed to origin/$WIP_BRANCH"
