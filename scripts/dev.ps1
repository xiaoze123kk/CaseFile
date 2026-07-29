$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

try {
    Write-Host "Starting CaseFile Web prototype at http://127.0.0.1:3000"
    pnpm dev:web
}
finally {
    Pop-Location
}
