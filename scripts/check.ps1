$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"

Push-Location $repoRoot
try {
    Write-Host "Checking repository structure..."

    $requiredPaths = @(
        "AGENT.md",
        "apps/web",
        "backend/alembic.ini",
        "backend/migrations/README.md",
        "backend/src/casefile/core",
        "backend/src/casefile/data_postgres/models",
        "contracts/schemas",
        "contracts/openapi.json",
        "fixtures"
    )

    foreach ($path in $requiredPaths) {
        if (-not (Test-Path $path)) {
            throw "Required repository path is missing: $path"
        }
    }

    & (Join-Path $PSScriptRoot "check-contracts.ps1")

    & (Join-Path $PSScriptRoot "check-migration-names.ps1")

    $python = if (Test-Path $venvPython) {
        $venvPython
    } else {
        (Get-Command python -ErrorAction Stop).Source
    }

    & $python -c "import alembic, sqlalchemy"
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependencies are missing. Install backend development dependencies first."
    }

    & $python -m compileall -q backend/src backend/migrations backend/tests contracts/generated/python/src
    if ($LASTEXITCODE -ne 0) {
        throw "Python compilation check failed."
    }

    & $python -m ruff check backend/src backend/migrations backend/tests
    if ($LASTEXITCODE -ne 0) {
        throw "Python Ruff check failed."
    }

    Push-Location $backendRoot
    try {
        & $python -m alembic heads
        if ($LASTEXITCODE -ne 0) {
            throw "Alembic chain check failed."
        }

        & $python -m alembic check
        if ($LASTEXITCODE -ne 0) {
            throw "Alembic model drift check failed."
        }

        & $python -m pytest tests/unit
        if ($LASTEXITCODE -ne 0) {
            throw "Backend unit tests failed."
        }
    } finally {
        Pop-Location
    }

    Write-Host "Repository checks passed."
} finally {
    Pop-Location
}
