#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Squash the WIP branch commit into a clean commit on main, then reset the
    WIP branch on top of the new main so the cycle starts fresh.

.PARAMETER Message
    The commit message for the clean main commit. Required.

.PARAMETER WipBranch
    The personal WIP branch name. Default: wip/nanobottie/devbox.

.EXAMPLE
    .\promote.ps1 -Message "feat: add checkpoint watcher"
    .\promote.ps1 -Message "feat: ..." -WipBranch wip/nanobottie/cloudvm
#>
param(
    [Parameter(Mandatory)]
    [string]$Message,
    [string]$WipBranch = "wip/nanobottie/devbox"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo     = Split-Path -Parent $scriptDir
Push-Location $repo

try {
    $currentBranch = git rev-parse --abbrev-ref HEAD 2>$null

    # ── sanity checks ─────────────────────────────────────────────────────────
    $dirty = git status --porcelain 2>$null
    if ($dirty) {
        Write-Error "Working tree is not clean. Stash or commit your changes before promoting."
    }

    if (-not (git show-ref --quiet refs/heads/$WipBranch 2>$null; $?)) {
        Write-Error "No local $WipBranch branch found. Nothing to promote."
    }

    $devLastMsg = git log $WipBranch -1 --pretty=%s 2>$null
    if ($devLastMsg -notlike "wip:*") {
        Write-Error "Last commit on $WipBranch doesn't look like a WIP (`"$devLastMsg`"). Aborting."
    }

    # ── switch to main ────────────────────────────────────────────────────────
    if ($currentBranch -ne "main") {
        git checkout main --quiet
    }

    # ── sync main from origin ─────────────────────────────────────────────────
    Write-Host "Syncing main from origin..." -ForegroundColor DarkGray
    git fetch origin --prune --quiet
    git pull origin main --ff-only --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to fast-forward main from origin. Diverged? Aborting."
    }
    Write-Host "✓ main synced with origin/main" -ForegroundColor DarkGray

    # ── squash-merge WIP branch into main ────────────────────────────────────
    git merge --squash $WipBranch --quiet
    git commit -m $Message
    Write-Host "✓ Committed to main: $Message" -ForegroundColor Green

    # ── push main ─────────────────────────────────────────────────────────────
    git push origin main --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Push to origin/main failed — not proceeding to rebase $WipBranch."
    }
    Write-Host "✓ Pushed origin/main" -ForegroundColor Green

    # ── rebase WIP branch on top of new main so WIP commit is gone ────────────
    git checkout $WipBranch --quiet
    git rebase main --quiet
    git push origin $WipBranch --force-with-lease --quiet
    Write-Host "✓ $WipBranch rebased onto new main and pushed" -ForegroundColor Green

    # ── return to original branch ─────────────────────────────────────────────
    if ($currentBranch -ne $WipBranch -and $currentBranch -ne "main") {
        git checkout $currentBranch --quiet
    }

    Write-Host ""
    Write-Host "Done. main is clean. $WipBranch is reset. Start the watcher and keep going." -ForegroundColor Cyan

} finally {
    Pop-Location
}
