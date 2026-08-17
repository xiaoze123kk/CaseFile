<#
.SYNOPSIS
Run the casefile-chat Phase 2 context acceptance for casefile-chat-context-v1.

.DESCRIPTION
Acceptance = quick gates + M1 DB canned 30-task baseline/rollout double run +
optional production-path live comparison:
1. Quick gates: ruff on Phase 2 files, strict mypy, non-PostgreSQL unit suite.
2. M1 baseline: CASEFILE_CHAT_CONTEXT_ROLLOUT off; all 30 T1 tasks must pass.
3. M1 rollout: CASEFILE_CHAT_CONTEXT_ROLLOUT on; all 30 T1 tasks must pass and
   real context.built aggregate executor input tokens must drop >= 50% versus
   the legacy full-injection render.
4. Live comparison (-LiveProvider openai|deepseek|fake): the same tasks run
   through the production path with legacy and v1 policies; rollout pass rate
   must not be lower than baseline.
The summary report defaults to tmp/chat-context-v1-acceptance-summary.json.
#>
param(
    [switch]$SkipQuickGates,
    [switch]$SkipM1Canned,
    [ValidateSet("", "openai", "deepseek", "fake")][string]$LiveProvider = "",
    [string]$LiveModel = "",
    [string]$LiveTaskIds = "golden-entity-question,golden-event-question,golden-issue-explanation,golden-edit-description,boundary-large-casefile",
    [string]$ReportPath = "tmp/chat-context-v1-acceptance-summary.json"
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
    "tmp/chat-context-v1-acceptance-summary.json"
} else {
    $ReportPath
}
$summaryFile = if ([System.IO.Path]::IsPathRooted($resolvedReportPath)) {
    $resolvedReportPath
} else {
    Join-Path $repoRoot $resolvedReportPath
}
$m1RolloutReport = Join-Path $repoRoot "tmp/chat-context-v1-m1-rollout.json"
$liveBaselineReport = Join-Path $repoRoot "tmp/chat-context-v1-live-baseline.json"
$liveRolloutReport = Join-Path $repoRoot "tmp/chat-context-v1-live-rollout.json"

$stepStatus = [ordered]@{
    quick_gates = "skipped"
    m1_baseline = "skipped"
    m1_rollout = "skipped"
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
                backend/src/casefile/agent_runtime/chat_tools.py `
                backend/src/casefile/agent_runtime/models.py `
                backend/src/casefile/agent_runtime/prompt.py `
                backend/src/casefile/agent_runtime/prompt_package.py `
                backend/src/casefile/agent_runtime/prompt_repository.py `
                backend/src/casefile/application/workflow_service.py `
                backend/src/casefile/worker/runtime.py `
                backend/tests/unit/test_context_phase2.py `
                backend/tests/unit/test_prompt_repository.py `
                backend/tests/integration/chat_outcome_canned_support.py `
                backend/tests/integration/test_chat_outcome_canned.py `
                backend/tests/integration/test_chat_context_phase2_acceptance.py `
                backend/tests/integration/test_chat_context_phase2_live_acceptance.py
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

    if (-not $SkipM1Canned) {
        Remove-Item Env:CASEFILE_CHAT_CONTEXT_ROLLOUT -ErrorAction SilentlyContinue
        $m1BaselineTests = @("tests/integration/test_chat_outcome_canned.py")
        Invoke-PytestStep -Name "M1 baseline (legacy, 30 tasks)" -TestPaths $m1BaselineTests -EnvOverride @()
        $stepStatus.m1_baseline = "passed"

        $m1RolloutTests = @(
            "tests/integration/test_chat_outcome_canned.py",
            "tests/integration/test_chat_context_phase2_acceptance.py"
        )
        $m1RolloutEnv = @(
            "CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v1",
            "CASEFILE_CHAT_CONTEXT_ACCEPTANCE_REPORT=$m1RolloutReport"
        )
        Invoke-PytestStep -Name "M1 rollout (casefile-chat-context-v1, 30 tasks + token gate)" -TestPaths $m1RolloutTests -EnvOverride $m1RolloutEnv
        $stepStatus.m1_rollout = "passed"
    }

    if (-not [string]::IsNullOrWhiteSpace($LiveProvider)) {
        Write-Host ""
        Write-Host "== Live production-path comparison (legacy vs rollout) ==" -ForegroundColor Cyan

        $liveTest = @("tests/integration/test_chat_context_phase2_live_acceptance.py")
        $liveBaselineEnv = @(
            "CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE=1",
            "CASEFILE_CHAT_CONTEXT_LIVE_PROVIDER=$LiveProvider",
            "CASEFILE_CHAT_CONTEXT_LIVE_TASK_IDS=$LiveTaskIds",
            "CASEFILE_CHAT_CONTEXT_LIVE_REPORT=$liveBaselineReport"
        )
        if (-not [string]::IsNullOrWhiteSpace($LiveModel)) {
            $liveBaselineEnv += "CASEFILE_CHAT_CONTEXT_LIVE_MODEL=$LiveModel"
        }
        Remove-Item Env:CASEFILE_CHAT_CONTEXT_ROLLOUT -ErrorAction SilentlyContinue
        Invoke-PytestStep -Name "Live baseline (legacy, $LiveProvider)" -TestPaths $liveTest -EnvOverride $liveBaselineEnv

        $liveRolloutEnv = @(
            "CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v1",
            "CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE=1",
            "CASEFILE_CHAT_CONTEXT_LIVE_PROVIDER=$LiveProvider",
            "CASEFILE_CHAT_CONTEXT_LIVE_TASK_IDS=$LiveTaskIds",
            "CASEFILE_CHAT_CONTEXT_LIVE_REPORT=$liveRolloutReport"
        )
        if (-not [string]::IsNullOrWhiteSpace($LiveModel)) {
            $liveRolloutEnv += "CASEFILE_CHAT_CONTEXT_LIVE_MODEL=$LiveModel"
        }
        Invoke-PytestStep -Name "Live rollout (casefile-chat-context-v1, $LiveProvider)" -TestPaths $liveTest -EnvOverride $liveRolloutEnv
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
    }

    $m1Report = Read-JsonReport $m1RolloutReport
    $summary = [ordered]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        policy_version = "casefile-chat-context-v1"
        prompt_version = "casefile-chat-v4"
        steps = $stepStatus
        m1 = if ($null -eq $m1Report) {
            $null
        } else {
            [ordered]@{
                task_count = $m1Report.task_count
                passed_count = $m1Report.passed_count
                aggregate_token_ratio = $m1Report.aggregate_token_ratio
                token_reduction = $m1Report.token_reduction
                rollout_tokens = $m1Report.rollout_tokens
                legacy_tokens = $m1Report.legacy_tokens
                gates = $m1Report.gates
            }
        }
        live = if ([string]::IsNullOrWhiteSpace($LiveProvider)) {
            $null
        } else {
            [ordered]@{
                provider = $LiveProvider
                baseline_pass_rate = $liveBaseline.pass_rate
                rollout_pass_rate = $liveRollout.pass_rate
                pass_rate_delta = [double]$liveRollout.pass_rate - [double]$liveBaseline.pass_rate
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
