param(
    [switch]$SkipPostgres
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

$hadDatabaseUrl = Test-Path Env:DATABASE_URL
$previousDatabaseUrl = $env:DATABASE_URL

Push-Location $repoRoot
try {
    & (Join-Path $PSScriptRoot "check-migration-names.ps1")

    & $python -c "import alembic, fastapi, jsonschema, psycopg, rfc8785, sqlalchemy"
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependencies are missing. Install backend development dependencies first."
    }

    & $python -m compileall -q backend/src backend/migrations backend/tests
    if ($LASTEXITCODE -ne 0) {
        throw "Python compilation check failed."
    }

    & $python -m ruff check --config backend/pyproject.toml backend/src backend/migrations backend/tests
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff check failed."
    }

    & $python -m mypy --config-file backend/pyproject.toml backend/src
    if ($LASTEXITCODE -ne 0) {
        throw "Mypy check failed."
    }

    & $python -m alembic -c backend/alembic.ini heads
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic chain check failed."
    }

    Push-Location $backendRoot
    try {
        if ($SkipPostgres) {
            & $python -m pytest -m "not postgres"
        } else {
            if ([string]::IsNullOrWhiteSpace($env:CASEFILE_TEST_DATABASE_URL)) {
                throw (
                    "CASEFILE_TEST_DATABASE_URL is required for PostgreSQL checks. " +
                    "It must point to a disposable database whose name ends in _test."
                )
            }

            $testDatabaseUri = $null
            $isAbsoluteDatabaseUrl = [Uri]::TryCreate(
                $env:CASEFILE_TEST_DATABASE_URL,
                [UriKind]::Absolute,
                [ref]$testDatabaseUri
            )
            $allowedDatabaseSchemes = @("postgresql", "postgresql+psycopg")
            if (
                -not $isAbsoluteDatabaseUrl -or
                $allowedDatabaseSchemes -notcontains $testDatabaseUri.Scheme
            ) {
                throw "Unsafe CASEFILE_TEST_DATABASE_URL: expected an absolute PostgreSQL URL."
            }

            $testDatabaseName = [Uri]::UnescapeDataString(
                $testDatabaseUri.AbsolutePath.Trim("/")
            )
            if (-not $testDatabaseName.EndsWith("_test", [StringComparison]::Ordinal)) {
                throw (
                    "Refusing destructive checks: CASEFILE_TEST_DATABASE_URL database name " +
                    "must end in _test. No database migration was run."
                )
            }

            $env:DATABASE_URL = $env:CASEFILE_TEST_DATABASE_URL
            & $python -m pytest
        }

        if ($LASTEXITCODE -ne 0) {
            throw "Backend tests failed."
        }
    } finally {
        Pop-Location
    }

    Write-Host "Repository checks passed."
} finally {
    if ($hadDatabaseUrl) {
        $env:DATABASE_URL = $previousDatabaseUrl
    } else {
        Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    }
    Pop-Location
}
