#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Amend the single rolling WIP commit on origin/$WipBranch.
    The WIP branch always stays exactly 1 commit ahead of main — force-pushed every time.
    Use promote.ps1 to squash WIP into a clean commit on main.

.PARAMETER Message
    Optional label stored in the commit message.

.PARAMETER WipBranch
    The personal WIP branch name. Default: wip/nanobottie/devbox.
    Override if working from a different machine/identity.

.EXAMPLE
    .\checkpoint.ps1
    .\checkpoint.ps1 -Message "before refactor"
    .\checkpoint.ps1 -WipBranch wip/nanobottie/cloudvm
#>
param(
    [string]$Message   = "",
    [string]$WipBranch = "wip/nanobottie/devbox"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo     = Split-Path -Parent $scriptDir
Push-Location $repo

try {
    # ── guard: no checkpointing during merge / rebase / cherry-pick ──────────
    $gitDir = git rev-parse --git-dir 2>$null
    $blockers = @(
        (Join-Path $gitDir "rebase-apply"),
        (Join-Path $gitDir "rebase-merge"),
        (Join-Path $gitDir "MERGE_HEAD"),
        (Join-Path $gitDir "CHERRY_PICK_HEAD"),
        (Join-Path $gitDir "REVERT_HEAD")
    )
    foreach ($blocker in $blockers) {
        if (Test-Path $blocker) {
            $name = Split-Path $blocker -Leaf
            Write-Host "Checkpoint skipped — $name detected. Resolve the operation first." -ForegroundColor Yellow
            exit 0
        }
    }

    # ── guard: conflict markers in tracked files ───────────────────────────────
    $unmerged = git diff --name-only --diff-filter=U 2>$null
    if ($unmerged) {
        Write-Host "Checkpoint skipped — unmerged paths detected:" -ForegroundColor Yellow
        $unmerged | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        exit 0
    }
    # ── guard: verify 'origin' remote exists ───────────────────────────────────────
    $originUrl = git remote get-url origin 2>$null
    if (-not $originUrl) {
        Write-Host "Checkpoint skipped — no 'origin' remote configured. Run: git remote add origin <url>" -ForegroundColor Yellow
        exit 0
    }
    # ── stash working tree so we can safely switch branches ────────────────────
    $currentBranch = git rev-parse --abbrev-ref HEAD 2>$null
    $devExists     = git branch --list $WipBranch
    $stashOut      = git stash --include-untracked 2>&1
    $didStash      = ($stashOut -notmatch "No local changes to save")

    # ── fetch + fast-forward main to origin/main ──────────────────────────────
    Write-Host "  Syncing main with origin/main..." -ForegroundColor DarkGray
    git fetch origin --prune --quiet
    git checkout main --quiet
    $ffResult = git pull origin main --ff-only 2>&1
    if ($LASTEXITCODE -ne 0) {
        # Can't fast-forward — local main has diverged. Restore and bail.
        if ($didStash) { git stash pop --quiet 2>$null }
        git checkout $currentBranch --quiet 2>$null
        Write-Host "Checkpoint aborted — local main has diverged from origin/main." -ForegroundColor Red
        Write-Host "Run: git checkout main && git log --oneline -5" -ForegroundColor DarkGray
        exit 1
    }

    # ── ensure WIP branch exists and rebase onto updated main ────────────────────────
    if (-not $devExists) {
        Write-Host "Creating local $WipBranch from main..." -ForegroundColor Cyan
        git checkout -b $WipBranch main
    } else {
        git checkout $WipBranch --quiet
        $rebaseResult = git rebase main 2>&1
        if ($LASTEXITCODE -ne 0) {
            git rebase --abort 2>$null
            if ($didStash) { git stash pop --quiet 2>$null }
            Write-Host "Checkpoint aborted — could not rebase $WipBranch onto main." -ForegroundColor Red
            Write-Host "Run: git checkout $WipBranch && git rebase main" -ForegroundColor DarkGray
            exit 1
        }
    }

    # ── restore stash onto dev ────────────────────────────────────────────────
    if ($didStash) {
        git stash pop --quiet
    }

    # ── stage everything (respects .gitignore) ────────────────────────────────
    git add -A

    $dirty = git status --porcelain
    if (-not $dirty) {
        Write-Host "Nothing to checkpoint — working tree clean." -ForegroundColor Yellow
        exit 0
    }

    # ── guard: refuse to commit secrets / junk ────────────────────────────────
    $staged = git diff --cached --name-only 2>$null
    $bad = $staged | Where-Object {
        $_ -match '(^|/)\.env$|\.pem$|\.key$|\.p12$|\.pfx$|secret|credential|id_rsa|id_ed25519'
    }
    if ($bad) {
        git restore --staged . 2>$null
        Write-Host "Checkpoint aborted — suspicious files staged:" -ForegroundColor Red
        $bad | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        Write-Host "Add them to .gitignore if intentional, then retry." -ForegroundColor DarkGray
        exit 1
    }

    # ── amend if last commit is a checkpoint, otherwise fresh commit ───────────
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm UTC")
    $label = if ($Message) { " · $Message" } else { "" }
    $commitMsg = "wip: $ts$label"

    $lastMsg = git log -1 --pretty=%s 2>$null
    if ($lastMsg -like "wip:*") {
        git commit --amend -m $commitMsg --quiet
        Write-Host "✓ Amended WIP: $commitMsg" -ForegroundColor Green
    } else {
        git commit -m $commitMsg --quiet
        Write-Host "✓ Committed WIP: $commitMsg" -ForegroundColor Green
    }

    # ── force-push (amend rewrites history — that's the point) ────────────────
    $hasUpstream = git rev-parse --abbrev-ref "${WipBranch}@{upstream}" 2>$null
    if ($hasUpstream) {
        git push origin $WipBranch --force-with-lease --quiet
    } else {
        git push origin $WipBranch -u --force-with-lease --quiet
    }
    Write-Host "✓ Pushed to origin/$WipBranch" -ForegroundColor Green

} finally {
    Pop-Location
}
