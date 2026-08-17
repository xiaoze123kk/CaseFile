<#
.SYNOPSIS
Run the casefile-chat Phase 3 rolling compaction acceptance.

.DESCRIPTION
Acceptance = quick gates + real-DB M1 gate + optional two-turn live comparison:
1. Quick gates: ruff on Phase 3 files, strict mypy, non-PostgreSQL unit suite.
2. M1 gate: CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v2 runs the
   production-path rolling compaction and patch-suggestion-legality tests.
3. Live comparison (-LiveProvider openai|deepseek|fake): the same two-turn
   tasks run with legacy full history and with v2 Thread Memory compaction;
   rollout pass rate must not be lower than baseline and every rollout thread
   must contain a persisted context state.
The summary report defaults to tmp/chat-context-v2-acceptance-summary.json.
#>
param(
    [switch]$SkipQuickGates,
    [switch]$SkipM1Gate,
    [ValidateSet("", "openai", "deepseek", "fake")][string]$LiveProvider = "",
    [string]$LiveModel = "",
    [string]$LiveTaskIds = "golden-entity-question,golden-event-question,golden-issue-explanation,golden-edit-description,boundary-large-casefile",
    [string]$ReportPath = "tmp/chat-context-v2-acceptance-summary.json"
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

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Missing .env. Run scripts/bootstrap.ps1 before acceptance."
}

foreach ($line in Get-Content -LiteralPath $envFile -Encoding utf8) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
        continue
    }
    $parts = $trimmed.Split("=", 2)
    if ($parts.Count -ne 2 -or $parts[0] -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "Invalid .env line: $line"
    }
    Set-Item -Path ("Env:" + $parts[0]) -Value $parts[1]
}

if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
    throw "DATABASE_URL is required."
}
if ([string]::IsNullOrWhiteSpace($env:CASEFILE_TEST_DATABASE_URL)) {
    throw "CASEFILE_TEST_DATABASE_URL is required and must point to a disposable *_test database."
}
if ([string]::IsNullOrWhiteSpace($env:CASEFILE_MASTER_KEY)) {
    throw "CASEFILE_MASTER_KEY is required."
}

$resolvedReportPath = if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    "tmp/chat-context-v2-acceptance-summary.json"
} else {
    $ReportPath
}
$summaryFile = if ([System.IO.Path]::IsPathRooted($resolvedReportPath)) {
    $resolvedReportPath
} else {
    Join-Path $repoRoot $resolvedReportPath
}
$liveBaselineReport = Join-Path $repoRoot "tmp/chat-context-v2-live-baseline.json"
$liveRolloutReport = Join-Path $repoRoot "tmp/chat-context-v2-live-rollout.json"

$stepStatus = [ordered]@{
    quick_gates = "skipped"
    m1_gate = "skipped"
    live = "skipped"
}

function Invoke-PytestStep([string]$Name, [string[]]$TestPaths, [string[]]$EnvOverride) {
    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    $previous = @{}
    foreach ($entry in $EnvOverride) {
        $parts = $entry.Split("=", 2)
        if ($parts.Count -ne 2) {
            throw "Invalid EnvOverride entry: $entry"
        }
        $previous[$parts[0]] = [Environment]::GetEnvironmentVariable($parts[0], "Process")
        Set-Item -Path ("Env:" + $parts[0]) -Value $parts[1]
    }
    Push-Location $backendRoot
    try {
        & $python -m pytest @TestPaths -q
        if ($LASTEXITCODE -ne 0) {
            throw "$Name failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
        foreach ($entry in $previous.GetEnumerator()) {
            if ($null -eq $entry.Value) {
                Remove-Item -Path ("Env:" + $entry.Key) -ErrorAction SilentlyContinue
            } else {
                Set-Item -Path ("Env:" + $entry.Key) -Value $entry.Value
            }
        }
    }
}

function Read-JsonReport([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
}

$failed = $false
try {
    if (-not $SkipQuickGates) {
        Write-Host ""
        Write-Host "== Quick gates: ruff / mypy / non-PostgreSQL tests ==" -ForegroundColor Cyan
        Push-Location $repoRoot
        try {
            & $python -m ruff check --config backend/pyproject.toml `
                backend/src/casefile/agent_runtime/context `
                backend/src/casefile/agent_runtime/models.py `
                backend/src/casefile/agent_runtime/prompt.py `
                backend/src/casefile/agent_runtime/prompt_package.py `
                backend/src/casefile/agent_runtime/prompt_repository.py `
                backend/src/casefile/agent_runtime/providers.py `
                backend/src/casefile/application/workflow_service.py `
                backend/src/casefile/benchmark/chat_context_eval.py `
                backend/src/casefile/data_postgres/session.py `
                backend/src/casefile/worker/runtime.py `
                backend/tests/unit/test_chat_context_eval.py `
                backend/tests/unit/test_context_phase3.py `
                backend/tests/unit/test_prompt_repository.py `
                backend/tests/integration/test_chat_context_phase3_acceptance.py `
                backend/tests/integration/test_chat_context_phase3_live_acceptance.py
            if ($LASTEXITCODE -ne 0) { throw "ruff failed with exit code $LASTEXITCODE." }
            & $python -m mypy --config-file backend/pyproject.toml backend/src
            if ($LASTEXITCODE -ne 0) { throw "mypy failed with exit code $LASTEXITCODE." }
        } finally {
            Pop-Location
        }
        Push-Location $backendRoot
        try {
            & $python -m pytest -m "not postgres" -q
            if ($LASTEXITCODE -ne 0) { throw "non-postgres tests failed with exit code $LASTEXITCODE." }
        } finally {
            Pop-Location
        }
        $stepStatus.quick_gates = "passed"
    }

    if (-not $SkipM1Gate) {
        $m1Tests = @("tests/integration/test_chat_context_phase3_acceptance.py")
        $m1Env = @("CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v2")
        Invoke-PytestStep -Name "M1 gate (rolling compaction + patch suggestion legality)" -TestPaths $m1Tests -EnvOverride $m1Env
        $stepStatus.m1_gate = "passed"
    }

    if (-not [string]::IsNullOrWhiteSpace($LiveProvider)) {
        Write-Host ""
        Write-Host "== Live two-turn comparison (legacy full history vs v2 Thread Memory) ==" -ForegroundColor Cyan

        $liveTest = @("tests/integration/test_chat_context_phase3_live_acceptance.py")
        $liveBaselineEnv = @(
            "CASEFILE_CHAT_CONTEXT_ROLLOUT=agent-focus-v1",
            "CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE=1",
            "CASEFILE_CHAT_CONTEXT_LIVE_PROVIDER=$LiveProvider",
            "CASEFILE_CHAT_CONTEXT_LIVE_TASK_IDS=$LiveTaskIds",
            "CASEFILE_CHAT_CONTEXT_LIVE_REPORT=$liveBaselineReport"
        )
        if (-not [string]::IsNullOrWhiteSpace($LiveModel)) {
            $liveBaselineEnv += "CASEFILE_CHAT_CONTEXT_LIVE_MODEL=$LiveModel"
        }
        Invoke-PytestStep -Name "Live baseline (legacy, $LiveProvider)" -TestPaths $liveTest -EnvOverride $liveBaselineEnv

        $liveRolloutEnv = @(
            "CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v2",
            "CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE=1",
            "CASEFILE_CHAT_CONTEXT_LIVE_PROVIDER=$LiveProvider",
            "CASEFILE_CHAT_CONTEXT_LIVE_TASK_IDS=$LiveTaskIds",
            "CASEFILE_CHAT_CONTEXT_LIVE_REPORT=$liveRolloutReport"
        )
        if (-not [string]::IsNullOrWhiteSpace($LiveModel)) {
            $liveRolloutEnv += "CASEFILE_CHAT_CONTEXT_LIVE_MODEL=$LiveModel"
        }
        Invoke-PytestStep -Name "Live rollout (casefile-chat-context-v2, $LiveProvider)" -TestPaths $liveTest -EnvOverride $liveRolloutEnv
        $stepStatus.live = "passed"

        $liveBaseline = Read-JsonReport $liveBaselineReport
        $liveRollout = Read-JsonReport $liveRolloutReport
        if ($null -eq $liveBaseline -or $null -eq $liveRollout) {
            throw "Live acceptance report is missing."
        }
        if ([double]$liveRollout.pass_rate -lt [double]$liveBaseline.pass_rate) {
            $failed = $true
            Write-Host ("LIVE PASS-RATE REGRESSION: baseline {0} -> rollout {1}" -f `
                $liveBaseline.pass_rate, $liveRollout.pass_rate) -ForegroundColor Red
        }
        if ([int]$liveRollout.compacted_threads -ne [int]$liveRollout.task_count) {
            $failed = $true
            Write-Host ("LIVE COMPACTION MISSING: {0}/{1} threads compacted" -f `
                $liveRollout.compacted_threads, $liveRollout.task_count) -ForegroundColor Red
        }
    }

    $summary = [ordered]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        policy_version = "casefile-chat-context-v2"
        prompt_version = "casefile-chat-v5"
        steps = $stepStatus
        live = if ([string]::IsNullOrWhiteSpace($LiveProvider)) {
            $null
        } else {
            [ordered]@{
                provider = $LiveProvider
                baseline_pass_rate = $liveBaseline.pass_rate
                rollout_pass_rate = $liveRollout.pass_rate
                pass_rate_delta = [double]$liveRollout.pass_rate - [double]$liveBaseline.pass_rate
                rollout_compacted_threads = $liveRollout.compacted_threads
                rollout_task_count = $liveRollout.task_count
                baseline_report = $liveBaselineReport
                rollout_report = $liveRolloutReport
            }
        }
        status = "pending"
    }
    $summary.status = if ($failed) { "failed" } else { "passed" }
    $summaryJson = $summary | ConvertTo-Json -Depth 8
    $summaryDirectory = Split-Path -Parent $summaryFile
    New-Item -ItemType Directory -Path $summaryDirectory -Force | Out-Null
    Set-Content -LiteralPath $summaryFile -Value $summaryJson -Encoding utf8
    Write-Host ""
    Write-Host $summaryJson
} catch {
    $failed = $true
    Write-Host $_.Exception.Message -ForegroundColor Red
    if (Test-Path -LiteralPath $summaryFile -PathType Leaf) {
        try {
            $summary = Get-Content -LiteralPath $summaryFile -Raw -Encoding utf8 | ConvertFrom-Json
            $summary.status = "failed"
            $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryFile -Encoding utf8
        } catch {
        }
    }
    throw
} finally {
    Remove-Item Env:CASEFILE_CHAT_CONTEXT_ROLLOUT -ErrorAction SilentlyContinue
    Remove-Item Env:CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE -ErrorAction SilentlyContinue
}
