<#
.SYNOPSIS
Run the casefile-chat Phase 3 rolling compaction acceptance.

.DESCRIPTION
Acceptance = quick gates + real-DB M1 gate + optional paired live comparison:
1. Quick gates: ruff on context/chat files, strict mypy, non-PostgreSQL unit suite.
2. M1 gate: CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v2 (or v3)
   runs the production-path rolling compaction and patch-suggestion-legality tests.
3. Live comparison (-LiveProvider openai|deepseek|fake): every task runs
   -LiveTrials trials with legacy full history and with the selected rollout
   policy, paired per task. pass@1 (any trial passes), pass^3 (all trials pass),
   and rollout compaction coverage are reported; a pass-rate regression or any
   task missing a compacted rollout thread still exits 1.
The summary report defaults to tmp/chat-context-v2-acceptance-summary.json.
#>
param(
    [switch]$SkipQuickGates,
    [switch]$SkipM1Gate,
    [ValidateSet("", "openai", "deepseek", "fake")][string]$LiveProvider = "",
    [string]$LiveModel = "",
    [ValidateSet(
        "casefile-chat-context-v1",
        "casefile-chat-context-v2",
        "casefile-chat-context-v3"
    )][string]$Rollout = "casefile-chat-context-v2",
    [string]$LiveTaskIds = "golden-entity-question,golden-event-question,golden-issue-explanation,golden-edit-description,boundary-large-casefile",
    [ValidateRange(1, 5)][int]$LiveTrials = 1,
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
$liveReportTag = switch ($Rollout) {
    "casefile-chat-context-v1" { "v1" }
    "casefile-chat-context-v3" { "v3" }
    default { "v2" }
}
$liveBaselineReport = Join-Path $repoRoot "tmp/chat-context-$liveReportTag-live-baseline.json"
$liveRolloutReport = Join-Path $repoRoot "tmp/chat-context-$liveReportTag-live-rollout.json"

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
                backend/src/casefile/agent_runtime/chat_reference_autofill.py `
                backend/src/casefile/agent_runtime/chat_routing.py `
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
                backend/tests/unit/test_chat_reference_autofill.py `
                backend/tests/unit/test_context_phase3.py `
                backend/tests/unit/test_prompt_repository.py `
                backend/tests/integration/test_chat_context_phase3_acceptance.py `
                backend/tests/integration/test_chat_context_phase3_live_acceptance.py `
                backend/tests/integration/test_chat_context_phase4_acceptance.py
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
        $m1Tests = if ($Rollout -eq "casefile-chat-context-v3") {
            @("tests/integration/test_chat_context_phase4_acceptance.py")
        } elseif ($Rollout -eq "casefile-chat-context-v1") {
            @("tests/integration/test_chat_context_phase2_acceptance.py")
        } else {
            @("tests/integration/test_chat_context_phase3_acceptance.py")
        }
        $m1Env = @("CASEFILE_CHAT_CONTEXT_ROLLOUT=$Rollout")
        Invoke-PytestStep -Name "M1 gate ($Rollout)" -TestPaths $m1Tests -EnvOverride $m1Env
        $stepStatus.m1_gate = "passed"
    }

    if (-not [string]::IsNullOrWhiteSpace($LiveProvider)) {
        Write-Host ""
        Write-Host "== Live paired comparison (legacy baseline vs $Rollout, $LiveTrials trial(s) per task) ==" -ForegroundColor Cyan

        $liveTest = @("tests/integration/test_chat_context_phase3_live_acceptance.py")
        $liveEnv = @(
            "CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE=1",
            "CASEFILE_CHAT_CONTEXT_LIVE_PROVIDER=$LiveProvider",
            "CASEFILE_CHAT_CONTEXT_LIVE_TASK_IDS=$LiveTaskIds",
            "CASEFILE_CHAT_CONTEXT_LIVE_TRIALS=$LiveTrials",
            "CASEFILE_CHAT_CONTEXT_LIVE_PAIRED=1",
            "CASEFILE_CHAT_CONTEXT_LIVE_ROLLOUT=$Rollout",
            "CASEFILE_CHAT_CONTEXT_LIVE_BASELINE_REPORT=$liveBaselineReport",
            "CASEFILE_CHAT_CONTEXT_LIVE_ROLLOUT_REPORT=$liveRolloutReport"
        )
        if (-not [string]::IsNullOrWhiteSpace($LiveModel)) {
            $liveEnv += "CASEFILE_CHAT_CONTEXT_LIVE_MODEL=$LiveModel"
        }
        Invoke-PytestStep -Name "Live paired comparison ($LiveProvider)" -TestPaths $liveTest -EnvOverride $liveEnv
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
            Write-Host ("LIVE COMPACTION MISSING: {0}/{1} tasks have all trials compacted" -f `
                $liveRollout.compacted_threads, $liveRollout.task_count) -ForegroundColor Red
        }
    }

    $summary = [ordered]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        policy_version = $Rollout
        prompt_version = if ($Rollout -eq "casefile-chat-context-v3") {
            "casefile-chat-v7"
        } elseif ($Rollout -eq "casefile-chat-context-v1") {
            "casefile-chat-v4"
        } else {
            "casefile-chat-v5"
        }
        steps = $stepStatus
        live = if ([string]::IsNullOrWhiteSpace($LiveProvider)) {
            $null
        } else {
            [ordered]@{
                provider = $LiveProvider
                trials = $LiveTrials
                baseline_pass_at_1 = $liveBaseline.pass_at_1
                baseline_pass_at_3 = $liveBaseline.pass_at_3
                rollout_pass_at_1 = $liveRollout.pass_at_1
                rollout_pass_at_3 = $liveRollout.pass_at_3
                rollout_reference_autofill = $liveRollout.reference_autofill
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
    if ($failed) {
        Write-Host ""
        Write-Host "Context acceptance gates failed; see summary above." -ForegroundColor Red
        exit 1
    }
    exit 0
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
