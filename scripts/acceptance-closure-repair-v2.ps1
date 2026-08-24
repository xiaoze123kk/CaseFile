[CmdletBinding()]
param(
    [Parameter()]
    [string]$HoldoutSuite = "var/benchmark/private/closure-repair-holdout-v2/suite.json",

    [Parameter()]
    [string]$OutputDirectory = "var/benchmark/m3-3-08/formal-v2"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$holdoutPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $HoldoutSuite))
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))

if ([string]::IsNullOrWhiteSpace($env:CASEFILE_TEST_DATABASE_URL)) {
    throw "CASEFILE_TEST_DATABASE_URL is required and must target a disposable *_test database."
}
if ([string]::IsNullOrWhiteSpace($env:CASEFILE_DEEPSEEK_API_KEY)) {
    throw "CASEFILE_DEEPSEEK_API_KEY is required for exact-model qualification."
}
if ([string]::IsNullOrWhiteSpace($env:CASEFILE_MASTER_KEY)) {
    throw "CASEFILE_MASTER_KEY is required for production credential persistence."
}
if (-not (Test-Path -LiteralPath $holdoutPath -PathType Leaf)) {
    throw "Private Holdout v2 suite was not found: $holdoutPath"
}
if ((Test-Path -LiteralPath $outputPath) -and (Get-ChildItem -LiteralPath $outputPath -Force)) {
    throw "Formal output directory must be empty: $outputPath"
}

Push-Location $repoRoot
try {
    Write-Host "[M3.3] Preflight: clean commit, frozen fingerprints, Pro model, *_test database"
    & uv run --project backend python -m casefile.benchmark.closure_repair_qualification `
        --repo-root $repoRoot `
        --holdout-suite $holdoutPath `
        --preflight
    if ($LASTEXITCODE -ne 0) {
        throw "Closure Repair qualification preflight failed."
    }

    Write-Host "[M3.3] Repository and PostgreSQL quality gates"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
    if ($LASTEXITCODE -ne 0) {
        throw "scripts/check.ps1 failed. Formal Provider trials were not started."
    }

    Write-Host "[M3.3] Rechecking the same clean revision after quality gates"
    & uv run --project backend python -m casefile.benchmark.closure_repair_qualification `
        --repo-root $repoRoot `
        --holdout-suite $holdoutPath `
        --preflight
    if ($LASTEXITCODE -ne 0) {
        throw "Post-check preflight failed. Formal Provider trials were not started."
    }

    Write-Host "[M3.3] Clean Dev 61x5, Holdout v2 42x5, then Backend Release 18x3"
    & uv run --project backend python -m casefile.benchmark.closure_repair_qualification `
        --repo-root $repoRoot `
        --holdout-suite $holdoutPath `
        --output-dir $outputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Closure Repair qualification did not pass. All completed attempts were retained."
    }
}
finally {
    Pop-Location
}
