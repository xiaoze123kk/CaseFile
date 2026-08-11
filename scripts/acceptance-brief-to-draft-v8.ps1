param(
    [ValidateRange(1, 100)][int]$Repeats = 30,
    [ValidateSet("deepseek", "openai")][string]$Provider = "deepseek",
    [ValidateSet("brief-to-draft-v8", "brief-to-draft-v9", "brief-to-draft-v10")][string]$PromptVersion = "brief-to-draft-v9",
    [string]$ReportPath = ""
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
    throw "Missing .env. Run scripts/bootstrap.ps1 before live acceptance."
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
    throw "DATABASE_URL is required to read the configured provider credential."
}
if ([string]::IsNullOrWhiteSpace($env:CASEFILE_TEST_DATABASE_URL)) {
    throw "CASEFILE_TEST_DATABASE_URL is required and must point to a disposable *_test database."
}
if ([string]::IsNullOrWhiteSpace($env:CASEFILE_MASTER_KEY)) {
    throw "CASEFILE_MASTER_KEY is required to exercise the stored provider credential path."
}

$resolvedReportPath = if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    "tmp\$PromptVersion-live-acceptance.json"
} else {
    $ReportPath
}
$reportFile = if ([System.IO.Path]::IsPathRooted($resolvedReportPath)) {
    $resolvedReportPath
} else {
    Join-Path $repoRoot $resolvedReportPath
}

$env:CASEFILE_RUN_LIVE_ACCEPTANCE = "1"
$env:CASEFILE_LIVE_ACCEPTANCE_REPEATS = "$Repeats"
$env:CASEFILE_LIVE_ACCEPTANCE_PROVIDER = $Provider
$env:CASEFILE_LIVE_ACCEPTANCE_PROMPT_VERSION = $PromptVersion
$env:CASEFILE_LIVE_ACCEPTANCE_REPORT_PATH = $reportFile

Push-Location $backendRoot
try {
    & $python -m pytest tests/integration/test_brief_to_draft_v8_live_acceptance.py -q
    if ($LASTEXITCODE -ne 0) {
        throw "$PromptVersion live acceptance did not pass. See $reportFile."
    }
} finally {
    Pop-Location
}
