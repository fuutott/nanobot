#!/usr/bin/env bash
# promote.sh — squash the WIP branch commit into a clean commit on main,
# then reset the WIP branch on top of the new main so the cycle starts fresh.
#
# Usage:
#   ./promote.sh "feat: add checkpoint watcher"
#   WIP_BRANCH=wip/nanobottie/cloudvm ./promote.sh "feat: ..."

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

MESSAGE="${1:-}"
WIP_BRANCH="${WIP_BRANCH:-wip/nanobottie/devbox}"

if [ -z "$MESSAGE" ]; then
    echo "Usage: ./promote.sh \"feat: your commit message\""
    exit 1
fi

CURRENT=$(git rev-parse --abbrev-ref HEAD)

# ── sanity checks ─────────────────────────────────────────────────────────────
if ! git show-ref --quiet "refs/heads/$WIP_BRANCH"; then
    echo "Error: no local $WIP_BRANCH branch found. Nothing to promote."
    exit 1
fi

DEV_LAST_MSG=$(git log "$WIP_BRANCH" -1 --pretty=%s 2>/dev/null || true)
if [[ "$DEV_LAST_MSG" != wip:* ]]; then
    echo "Error: last commit on $WIP_BRANCH doesn't look like a WIP (\"$DEV_LAST_MSG\"). Aborting."
    exit 1
fi

dirty=$(git status --porcelain 2>/dev/null || true)
if [ -n "$dirty" ]; then
    echo "Error: working tree is not clean. Stash or commit your changes before promoting."
    exit 1
fi

# ── switch to main ────────────────────────────────────────────────────────────
[ "$CURRENT" != "main" ] && git checkout main --quiet

# ── sync main from origin ─────────────────────────────────────────────────────
echo "Syncing main from origin..."
git fetch origin --prune --quiet
if ! git pull origin main --ff-only --quiet; then
    echo "Error: failed to fast-forward main from origin. Diverged? Aborting."
    exit 1
fi
echo "✓ main synced with origin/main"

# ── squash-merge WIP branch into main ────────────────────────────────────────
git merge --squash "$WIP_BRANCH" --quiet
git commit -m "$MESSAGE"
echo "✓ Committed to main: $MESSAGE"

# ── push main ─────────────────────────────────────────────────────────────────
if ! git push origin main --quiet; then
    echo "Error: push to origin/main failed — not proceeding to rebase $WIP_BRANCH."
    exit 1
fi
echo "✓ Pushed origin/main"

# ── rebase WIP branch on top of new main so the WIP commit is gone ────────────
git checkout "$WIP_BRANCH" --quiet
git rebase main --quiet
git push origin "$WIP_BRANCH" --force-with-lease --quiet
echo "✓ $WIP_BRANCH rebased onto new main and pushed"

# ── return to original branch ─────────────────────────────────────────────────
if [ "$CURRENT" != "$WIP_BRANCH" ] && [ "$CURRENT" != "main" ]; then
    git checkout "$CURRENT" --quiet
fi

echo ""
echo "Done. main is clean. $WIP_BRANCH is reset. Start the watcher and keep going."
