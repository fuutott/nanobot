#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Poll git and auto-checkpoint to origin/dev when content has been stable
    for at least $DebounceSeconds.

    Debounce is based on a SHA1 hash of 'git diff HEAD' + untracked file list,
    not just 'git status' text. This means continuously editing a file resets
    the clock even if the status line (' M file.py') never changes.

.PARAMETER DebounceSeconds
    Seconds of identical diff content before committing. Default: 30.

.PARAMETER PollSeconds
    How often to poll. Default: 5.

.PARAMETER CheckpointScript
    Path to checkpoint.ps1. Defaults to sibling in the same directory.

.EXAMPLE
    .\watch-checkpoint.ps1
    .\watch-checkpoint.ps1 -DebounceSeconds 60
#>
param(
    [int]$DebounceSeconds = 30,
    [int]$PollSeconds = 5,
    [string]$CheckpointScript = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo      = Split-Path -Parent $scriptDir
if (-not $CheckpointScript) {
    $CheckpointScript = Join-Path $scriptDir "checkpoint.ps1"
}

Push-Location $repo

$sha1 = [System.Security.Cryptography.SHA1]::Create()

function Get-DiffHash {
    # Hash staged + unstaged deltas + untracked file list separately so every
    # real content change (stage, edit, new file) resets the debounce clock.
    $cached    = (git diff --cached 2>$null) -join "`n"
    $work      = (git diff 2>$null) -join "`n"
    $untracked = (git ls-files --others --exclude-standard 2>$null) -join "`n"
    $raw = "$cached`n---work---`n$work`n---untracked---`n$untracked"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($raw)
    return [System.BitConverter]::ToString($sha1.ComputeHash($bytes))
}

try {
    Write-Host "Watching $repo" -ForegroundColor Cyan
    Write-Host "Checkpoint fires after ${DebounceSeconds}s of stable content (poll every ${PollSeconds}s)." -ForegroundColor DarkGray
    Write-Host "Press Ctrl+C to stop.`n" -ForegroundColor DarkGray

    $lastStatus    = ""     # Phase 1: cheap status text
    $lastHash      = ""     # Phase 2: expensive diff hash (only when status stable)
    $lastChangedAt = $null  # when we last saw any change

    while ($true) {
        Start-Sleep -Seconds $PollSeconds

        # ── Phase 1: cheap — git status --porcelain ──────────────────────────────────
        $status = (git status --porcelain 2>$null) -join "`n"

        if (-not $status) {
            if ($lastStatus) {
                Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Working tree clean." -ForegroundColor DarkGray
                $lastStatus = ""; $lastHash = ""; $lastChangedAt = $null
            }
            continue
        }

        if ($status -ne $lastStatus) {
            # Status text changed — we already know something is different.
            # Reset the clock and skip the expensive hash this cycle.
            $isFirst    = -not $lastStatus
            $lastStatus = $status
            $lastHash   = ""   # invalidate; will recompute once status settles
            $lastChangedAt = [datetime]::UtcNow

            if ($isFirst) {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Changes detected:" -ForegroundColor DarkGray
                $status -split "`n" | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
            } else {
                Write-Host "`r[$(Get-Date -Format 'HH:mm:ss')] Status changed — debounce reset.        " -ForegroundColor DarkGray
            }
            continue   # <─ skip hash this poll
        }

        # ── Phase 2: status stable — now pay for the diff hash ───────────────────────
        $hash = Get-DiffHash

        if ($hash -ne $lastHash) {
            # Same status text but different content (mid-edit, same file)
            $lastHash      = $hash
            $lastChangedAt = [datetime]::UtcNow
            Write-Host "`r[$(Get-Date -Format 'HH:mm:ss')] Content changed — debounce reset.         " -ForegroundColor DarkGray
            continue
        }

        # ── Truly stable — check debounce countdown ────────────────────────────────
        $elapsed   = ([datetime]::UtcNow - $lastChangedAt).TotalSeconds
        $remaining = [math]::Ceiling($DebounceSeconds - $elapsed)

        if ($elapsed -ge $DebounceSeconds) {
            Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Stable for ${DebounceSeconds}s — checkpointing..." -ForegroundColor Yellow
            try {
                & pwsh -NonInteractive -File $CheckpointScript
                $lastStatus = ""; $lastHash = ""; $lastChangedAt = $null
            } catch {
                Write-Host "  Checkpoint failed: $_" -ForegroundColor Red
            }
            Write-Host ""
        } else {
            Write-Host "`r  (content stable, checkpoint in ${remaining}s)  " -NoNewline -ForegroundColor DarkGray
        }
    }
} finally {
    $sha1.Dispose()
    Pop-Location
    Write-Host "`nWatcher stopped." -ForegroundColor DarkGray
}
