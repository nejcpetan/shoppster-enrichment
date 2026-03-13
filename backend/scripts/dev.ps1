# Dev server with auto-restart on crash
# Usage: from backend/ run: .venv/Scripts/powershell -File scripts/dev.ps1

$ErrorActionPreference = "Continue"
$restarts = 0

while ($true) {
    if ($restarts -gt 0) {
        Write-Host ""
        Write-Host "[$restarts restart(s)] Restarting in 2s..." -ForegroundColor Yellow
        Start-Sleep -Seconds 2
    }

    Write-Host "Starting backend server..." -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" main.py
    $restarts++
}
