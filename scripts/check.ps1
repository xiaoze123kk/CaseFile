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
$hadContextRollout = Test-Path Env:CASEFILE_CHAT_CONTEXT_ROLLOUT
$previousContextRollout = $env:CASEFILE_CHAT_CONTEXT_ROLLOUT

$checkStartedAt = [DateTimeOffset]::UtcNow
$checkClock = [Diagnostics.Stopwatch]::StartNew()
$checkTimings = [Collections.Generic.List[object]]::new()
$checkPassed = $false
$checkRunId = $checkStartedAt.ToString("yyyyMMddTHHmmssfffZ") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$checkReportRoot = Join-Path $repoRoot ("var/checks/" + $checkRunId)
New-Item -ItemType Directory -Path $checkReportRoot -Force | Out-Null

function Invoke-CheckStage {
    param([string]$Name, [string]$Category, [scriptblock]$Action, [string]$PytestReport = "")
    $stageClock = [Diagnostics.Stopwatch]::StartNew()
    $stageStatus = "failed"
    try {
        & $Action
        $stageStatus = "passed"
    } finally {
        $stageClock.Stop()
        $checkTimings.Add([ordered]@{
            name = $Name
            category = $Category
            status = $stageStatus
            seconds = $stageClock.Elapsed.TotalSeconds
            pytest_report = $PytestReport
        })
        Write-Host ("[timing] {0}: {1:N3}s ({2})" -f $Name, $stageClock.Elapsed.TotalSeconds, $stageStatus)
    }
}

Push-Location $repoRoot
try {
    Invoke-CheckStage "static" "static" {
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
    }

    Push-Location $backendRoot
    try {
        # Full deterministic matrices run once through pytest. Standalone benchmark
        # CLIs remain available for explicit reports and release qualification.
        Invoke-CheckStage "chat-goal" "evaluation" {
            & $python -m casefile.benchmark.chat_goal_gate
            if ($LASTEXITCODE -ne 0) {
                throw "CaseFile chat Goal deterministic/Fake gate failed."
            }
        }

        if ($SkipPostgres) {
            $pytestReport = Join-Path $checkReportRoot "pytest.json"
            Invoke-CheckStage "pytest" "tests" -PytestReport $pytestReport -Action {
                & $python -m pytest -m "not postgres" --durations=20 --timing-report $pytestReport
                if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
            }
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
            $pytestReport = Join-Path $checkReportRoot "pytest.json"
            Invoke-CheckStage "pytest" "tests" -PytestReport $pytestReport -Action {
                & $python -m pytest --durations=20 --timing-report $pytestReport
                if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
            }

            $env:CASEFILE_CHAT_CONTEXT_ROLLOUT = "casefile-chat-context-v2"
            $pytestReport = Join-Path $checkReportRoot "pytest-phase3.json"
            Invoke-CheckStage "pytest-phase3" "tests" -PytestReport $pytestReport -Action {
                & $python -m pytest tests/integration/test_chat_context_phase3_acceptance.py --durations=20 --timing-report $pytestReport
                if ($LASTEXITCODE -ne 0) { throw "Phase 3 rolling compaction M1 gate failed." }
            }

            $env:CASEFILE_CHAT_CONTEXT_ROLLOUT = "casefile-chat-context-v3"
            $pytestReport = Join-Path $checkReportRoot "pytest-phase4.json"
            Invoke-CheckStage "pytest-phase4" "tests" -PytestReport $pytestReport -Action {
                & $python -m pytest tests/integration/test_chat_context_phase4_acceptance.py --durations=20 --timing-report $pytestReport
                if ($LASTEXITCODE -ne 0) { throw "Phase 4 Context Tools M1 gate failed." }
            }
        }

    } finally {
        Pop-Location
    }

    $checkPassed = $true
    Write-Host "Repository checks passed."
} finally {
    if ($hadDatabaseUrl) {
        $env:DATABASE_URL = $previousDatabaseUrl
    } else {
        Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    }
    if ($hadContextRollout) {
        $env:CASEFILE_CHAT_CONTEXT_ROLLOUT = $previousContextRollout
    } else {
        Remove-Item Env:CASEFILE_CHAT_CONTEXT_ROLLOUT -ErrorAction SilentlyContinue
    }
    Pop-Location
    $checkClock.Stop()
    $categorySeconds = [ordered]@{}
    foreach ($category in @("static", "evaluation", "tests")) {
        $seconds = 0.0
        foreach ($stage in $checkTimings) {
            if ($stage.category -eq $category) { $seconds += $stage.seconds }
        }
        $categorySeconds[$category] = $seconds
    }
    $summary = [ordered]@{
        schema_version = 1
        started_at = $checkStartedAt.ToString("o")
        finished_at = [DateTimeOffset]::UtcNow.ToString("o")
        status = $(if ($checkPassed) { "passed" } else { "failed" })
        skip_postgres = [bool]$SkipPostgres
        elapsed_seconds = $checkClock.Elapsed.TotalSeconds
        category_seconds = $categorySeconds
        stages = @($checkTimings.ToArray())
    }
    $summaryPath = Join-Path $checkReportRoot "summary.json"
    try {
        [IO.File]::WriteAllText($summaryPath, ($summary | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
        Write-Host ("Check timings: {0} (total {1:N3}s)" -f $summaryPath, $checkClock.Elapsed.TotalSeconds)
    } catch {
        Write-Warning ("Could not write check timings: {0}" -f $_.Exception.Message)
    }
}
