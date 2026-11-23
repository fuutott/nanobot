# docker run --rm -v "$env:USERPROFILE\.nanobot:/home/nanobottie/.nanobot" nanobot onboard

# Build first while the old container keeps running; only swap containers once
# the new image built successfully. Native exe failures don't throw in PS 5.1,
# so every docker call checks $LASTEXITCODE explicitly.

Set-Location $PSScriptRoot
$compose = "docker-compose.standalone.yml"

function Stop-Redo([string]$message) {
    Write-Host "redo.ps1: $message" -ForegroundColor Red
    exit 1
}

docker compose -f $compose build --progress=plain --build-arg BUILD_BRIDGE=0
if ($LASTEXITCODE -ne 0) {
    Stop-Redo "build failed (exit $LASTEXITCODE); the running container was left untouched."
}

docker compose -f $compose down
if ($LASTEXITCODE -ne 0) {
    Stop-Redo "'docker compose down' failed (exit $LASTEXITCODE); new image is built but not deployed."
}

# Best-effort removal of a stray container with the same name (not managed by compose).
docker rm -f nanobot-gateway 2>$null
if ($LASTEXITCODE -ne 0) {
    Stop-Redo "'docker rm -f nanobot-gateway' failed (exit $LASTEXITCODE); gateway is DOWN."
}

docker compose -f $compose up -d
if ($LASTEXITCODE -ne 0) {
    Stop-Redo "'docker compose up -d' failed (exit $LASTEXITCODE); gateway is DOWN."
}

Write-Host "redo.ps1: gateway rebuilt and started." -ForegroundColor Green
