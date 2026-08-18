<#
.SYNOPSIS
Run the Phase 4 multi-tier context benchmark and acceptance gates.

.DESCRIPTION
Deterministic and production gates for autonomous context management:
1. chat-outcome calibration (M0 reference trials).
2. Context baseline + Boundary continuation gate (M0/Boundary).
3. Four-tier deterministic A/B report (Step 4.3): legacy full,
   context-v1 deterministic, +rolling compaction, +dashboard/tools.
4. Quick backend gates: ruff, mypy, non-PostgreSQL pytest suite.
5. PostgreSQL acceptance when CASEFILE_TEST_DATABASE_URL is present:
   full pytest plus Phase 2/3/4 rollout gates.
6. Live provider acceptance when -LiveProvider is passed; delegates to
   scripts/acceptance-chat-context-v2.ps1.
The comparison report defaults to tmp/context-benchmark-summary.json.
#>
param(
    [switch]$SkipQuickGates,
    [switch]$SkipPostgresGates,
    [ValidateSet("", "openai", "deepseek")][string]$LiveProvider = "",
    [string]$LiveModel = "",
    [ValidateSet(
        "casefile-chat-context-v1",
        "casefile-chat-context-v2",
        "casefile-chat-context-v3"
    )][string]$LiveRollout = "casefile-chat-context-v2",
    [string]$ReportPath = "tmp/context-benchmark-summary.json"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$envFile = Join-Path $repoRoot ".env"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

function Invoke-PythonStep([string]$Name, [string[]]$Arguments, [string]$WorkingDirectory = $repoRoot) {
    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    Push-Location $WorkingDirectory
    try {
        & $python @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Name failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
}

function Invoke-PytestStep([string]$Name, [string[]]$TestPaths, [string[]]$EnvOverride = @()) {
    $previous = @{}
    foreach ($entry in $EnvOverride) {
        $parts = $entry.Split("=", 2)
        if ($parts.Count -ne 2) {
            throw "Invalid EnvOverride entry: $entry"
        }
        $previous[$parts[0]] = [Environment]::GetEnvironmentVariable($parts[0], "Process")
        Set-Item -Path ("Env:" + $parts[0]) -Value $parts[1]
    }
    try {
        $arguments = @("-m", "pytest") + $TestPaths
        Invoke-PythonStep $Name $arguments -WorkingDirectory $backendRoot
    } finally {
        foreach ($entry in $previous.GetEnumerator()) {
            if ($null -eq $entry.Value) {
                Remove-Item -Path ("Env:" + $entry.Key) -ErrorAction SilentlyContinue
            } else {
                Set-Item -Path ("Env:" + $entry.Key) -Value $entry.Value
            }
        }
    }
}

$summary = [ordered]@{
    generated_at = (Get-Date).ToString("o")
    repo = (git -C $repoRoot rev-parse --short HEAD 2>$null)
    steps = [ordered]@{}
}
$tierReportRel = "var/benchmark/context-tiers-v1.json"
$tierReportAbs = Join-Path $backendRoot $tierReportRel

function Complete-Step([string]$Name, [string]$Status, [string]$Detail = "") {
    $summary.steps[$Name] = [ordered]@{
        status = $Status
        detail = $Detail
    }
}

Push-Location $repoRoot
try {
    # 1. Deterministic gates always run.
    Invoke-PythonStep "Chat outcome M0 calibration" @(
        "-m", "casefile.benchmark", "chat-outcome", "--mode", "calibrate"
    )
    Complete-Step "m0_calibration" "passed"

    Invoke-PythonStep "Context baseline + Boundary continuation" @(
        "-m", "casefile.benchmark.chat_context_eval",
        "--boundary-report-path", "var/benchmark/context-boundary-v1.json",
        "--gate-boundary"
    ) -WorkingDirectory $backendRoot
    Complete-Step "boundary_eval" "passed"

    Invoke-PythonStep "Four-tier deterministic context A/B" @(
        "-m", "casefile.benchmark.context_tier_benchmark",
        "--report-path", $tierReportRel,
        "--gate"
    ) -WorkingDirectory $backendRoot
    $tierReport = Get-Content -LiteralPath $tierReportAbs -Raw -Encoding utf8 | ConvertFrom-Json
    $tierDetail = [ordered]@{}
    foreach ($tier in $tierReport.tiers) {
        $tierDetail[$tier.tier_id] = [ordered]@{
            peak_input_tokens = $tier.peak_input_tokens
            total_input_tokens = $tier.total_input_tokens
            fallback_count = $tier.fallback_count
            guardrail_violations = $tier.guardrail_violations
        }
    }
    $summary.tier_comparison = $tierDetail
    Complete-Step "tier_comparison" "passed"

    # 2. Quick backend gates.
    if (-not $SkipQuickGates) {
        Invoke-PythonStep "Ruff" @(
            "-m", "ruff", "check", "--config", "backend/pyproject.toml",
            "backend/src", "backend/migrations", "backend/tests"
        )
        Invoke-PythonStep "Mypy" @(
            "-m", "mypy", "--config-file", "backend/pyproject.toml", "backend/src"
        )
        Invoke-PythonStep "Compile all" @("-m", "compileall", "-q", "backend/src", "backend/migrations", "backend/tests")
        Invoke-PytestStep "Non-PostgreSQL suite" @("-m", "not postgres", "-q")
        Complete-Step "quick_gates" "passed"
    } else {
        Complete-Step "quick_gates" "skipped"
    }

    # 3. PostgreSQL production gates.
    if (-not $SkipPostgresGates) {
        if ([string]::IsNullOrWhiteSpace($env:CASEFILE_TEST_DATABASE_URL)) {
            Complete-Step "postgres_gates" "skipped" "CASEFILE_TEST_DATABASE_URL not set"
        } else {
            $uri = $null
            $isUri = [Uri]::TryCreate(
                $env:CASEFILE_TEST_DATABASE_URL,
                [UriKind]::Absolute,
                [ref]$uri
            )
            if (-not $isUri -or $uri.Scheme -notin @("postgresql", "postgresql+psycopg")) {
                throw "Unsafe CASEFILE_TEST_DATABASE_URL."
            }
            $dbName = [Uri]::UnescapeDataString($uri.AbsolutePath.Trim("/"))
            if (-not $dbName.EndsWith("_test", [StringComparison]::Ordinal)) {
                throw "CASEFILE_TEST_DATABASE_URL database name must end in _test."
            }
            Invoke-PytestStep "Full PostgreSQL suite" @("-q") @("DATABASE_URL=$($env:CASEFILE_TEST_DATABASE_URL)")
            Invoke-PytestStep "Phase 2 rollout gate" @(
                "tests/integration/test_chat_context_phase2_acceptance.py", "-q"
            ) @(
                "DATABASE_URL=$($env:CASEFILE_TEST_DATABASE_URL)",
                "CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v1"
            )
            Invoke-PytestStep "Phase 3 rollout gate" @(
                "tests/integration/test_chat_context_phase3_acceptance.py", "-q"
            ) @(
                "DATABASE_URL=$($env:CASEFILE_TEST_DATABASE_URL)",
                "CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v2"
            )
            Invoke-PytestStep "Phase 4 rollout gate" @(
                "tests/integration/test_chat_context_phase4_acceptance.py", "-q"
            ) @(
                "DATABASE_URL=$($env:CASEFILE_TEST_DATABASE_URL)",
                "CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v3"
            )
            Complete-Step "postgres_gates" "passed"
        }
    } else {
        Complete-Step "postgres_gates" "skipped"
    }

    # 4. Live provider acceptance delegates to the maintained Phase 3 script.
    if (-not [string]::IsNullOrWhiteSpace($LiveProvider)) {
        if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
            throw "Missing .env for live acceptance."
        }
        foreach ($line in Get-Content -LiteralPath $envFile -Encoding utf8) {
            $trimmed = $line.Trim()
            if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
                continue
            }
            $parts = $trimmed.Split("=", 2)
            if ($parts.Count -ne 2 -or $parts[0] -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
                continue
            }
            Set-Item -Path ("Env:" + $parts[0]) -Value $parts[1]
        }
        $liveKeyNames = @()
        if ($LiveProvider -eq "openai") {
            $liveKeyNames = @(
                "CASEFILE_CHAT_CONTEXT_LIVE_API_KEY",
                "CASEFILE_OPENAI_API_KEY",
                "OPENAI_API_KEY"
            )
        } elseif ($LiveProvider -eq "deepseek") {
            $liveKeyNames = @(
                "CASEFILE_CHAT_CONTEXT_LIVE_API_KEY",
                "CASEFILE_DEEPSEEK_API_KEY",
                "DEEPSEEK_API_KEY"
            )
        }
        $hasLiveKey = $false
        foreach ($name in $liveKeyNames) {
            $value = [Environment]::GetEnvironmentVariable($name, "Process")
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                $hasLiveKey = $true
                break
            }
        }
        if (-not $hasLiveKey) {
            Complete-Step "live_acceptance" "skipped" "no live API key env for $LiveProvider"
            Write-Host "Live acceptance skipped: no API key env for $LiveProvider." -ForegroundColor Yellow
        } else {
            $acceptanceScript = Join-Path $PSScriptRoot "acceptance-chat-context-v2.ps1"
            if ([string]::IsNullOrWhiteSpace($LiveModel)) {
                & $acceptanceScript -SkipQuickGates -SkipM1Gate `
                    -LiveProvider $LiveProvider -Rollout $LiveRollout
            } else {
                & $acceptanceScript -SkipQuickGates -SkipM1Gate `
                    -LiveProvider $LiveProvider -LiveModel $LiveModel -Rollout $LiveRollout
            }
            if ($LASTEXITCODE -ne 0) {
                throw "Live context acceptance failed."
            }
            Complete-Step "live_acceptance" "passed"
        }
    } else {
        Complete-Step "live_acceptance" "skipped"
    }

    $resolvedReport = if ([System.IO.Path]::IsPathRooted($ReportPath)) {
        $ReportPath
    } else {
        Join-Path $repoRoot $ReportPath
    }
    $reportParent = Split-Path -Parent $resolvedReport
    if (-not [string]::IsNullOrWhiteSpace($reportParent)) {
        New-Item -ItemType Directory -Force -Path $reportParent | Out-Null
    }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resolvedReport -Encoding utf8
    Write-Host ""
    Write-Host "Context benchmark passed. Report: $resolvedReport" -ForegroundColor Green
} finally {
    Pop-Location
}
