#!/usr/bin/env bash
# watch-checkpoint.sh — poll git and auto-checkpoint to origin/dev when
# content has been stable for at least $DEBOUNCE seconds.
#
# Debounce is based on a SHA1 hash of 'git diff HEAD' + untracked file list,
# not just 'git status' text. Continuously editing a file resets the clock
# even if the status line (' M file.py') never changes.
#
# No external deps — uses only git and standard coreutils.
#
# Usage:
#   ./watch-checkpoint.sh              # 30s debounce, 5s poll
#   DEBOUNCE=60 POLL=10 ./watch-checkpoint.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECKPOINT="${SCRIPT_DIR}/checkpoint.sh"
DEBOUNCE="${DEBOUNCE:-30}"
POLL="${POLL:-5}"

cd "$REPO_DIR"

# Pick available hasher: sha1sum (Linux) or shasum (macOS)
if command -v sha1sum &>/dev/null; then
    HASHER="sha1sum"
elif command -v shasum &>/dev/null; then
    HASHER="shasum"
else
    # md5 is always available as a last resort (collision-safe enough here)
    HASHER="md5sum 2>/dev/null || md5"
fi

diff_hash() {
    # Hash staged + unstaged deltas + untracked file list separately so every
    # real content change (stage, edit, new file) resets the debounce clock.
    { git diff --cached 2>/dev/null
      echo "---work---"
      git diff 2>/dev/null
      echo "---untracked---"
      git ls-files --others --exclude-standard 2>/dev/null
    } | $HASHER | cut -d' ' -f1
}

cleanup() {
    echo -e "\nWatcher stopped."
    exit 0
}
trap cleanup INT TERM

echo "Watching $REPO_DIR"
echo "Checkpoint fires after ${DEBOUNCE}s of stable content (poll every ${POLL}s)."
echo "Press Ctrl+C to stop."
echo ""

last_status=""    # Phase 1: cheap status text
last_hash=""      # Phase 2: expensive diff hash (only when status stable)
last_changed_at=""

while true; do
    sleep "$POLL"

    # ── Phase 1: cheap — git status --porcelain ──────────────────────────────────
    status=$(git status --porcelain 2>/dev/null || true)

    if [ -z "$status" ]; then
        if [ -n "$last_status" ]; then
            echo ""
            echo "[$(date '+%H:%M:%S')] Working tree clean."
            last_status=""; last_hash=""; last_changed_at=""
        fi
        continue
    fi

    if [ "$status" != "$last_status" ]; then
        # Status text changed — we already know something is different.
        # Reset the clock and skip the expensive hash this cycle.
        is_first=$([ -z "$last_status" ] && echo "yes" || echo "no")
        last_status="$status"
        last_hash=""   # invalidate; will recompute once status settles
        last_changed_at=$(date +%s)

        if [ "$is_first" = "yes" ]; then
            echo "[$(date '+%H:%M:%S')] Changes detected:"
            echo "$status" | sed 's/^/  /'
        else
            printf "\r[%s] Status changed \u2014 debounce reset.        \n" "$(date '+%H:%M:%S')"
        fi
        continue   # <─ skip hash this poll
    fi

    # ── Phase 2: status stable — now pay for the diff hash ───────────────────────
    hash=$(diff_hash)

    if [ "$hash" != "$last_hash" ]; then
        # Same status text but different content (mid-edit, same file)
        last_hash="$hash"
        last_changed_at=$(date +%s)
        printf "\r[%s] Content changed \u2014 debounce reset.         \n" "$(date '+%H:%M:%S')"
        continue
    fi

    # ── Truly stable — check debounce countdown ───────────────────────────────────
    now=$(date +%s)
    elapsed=$(( now - last_changed_at ))
    remaining=$(( DEBOUNCE - elapsed ))

    if [ "$elapsed" -ge "$DEBOUNCE" ]; then
        echo ""
        echo "[$(date '+%H:%M:%S')] Stable for ${DEBOUNCE}s \u2014 checkpointing..."
        if bash "$CHECKPOINT"; then
            last_status=""; last_hash=""; last_changed_at=""
        else
            echo "  Checkpoint failed \u2014 will retry next cycle."
        fi
        echo ""
    else
        printf "\r  (content stable, checkpoint in %ds)  " "$remaining"
    fi
done
