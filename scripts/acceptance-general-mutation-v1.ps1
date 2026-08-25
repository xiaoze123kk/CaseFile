[CmdletBinding()]
param(
    [Parameter()]
    [string]$HoldoutSuite = "backend/var/benchmark/private/general-mutation-holdout-v1/suite.json",

    [Parameter()]
    [string]$OutputDirectory = "backend/var/benchmark/m3.4-07f-formal"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$holdoutPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $HoldoutSuite))
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))

if ([string]::IsNullOrWhiteSpace($env:CASEFILE_TEST_DATABASE_URL)) {
    throw "CASEFILE_TEST_DATABASE_URL is required and must target a disposable *_test database."
}
if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
    throw "DATABASE_URL is required to read actor 1's saved DeepSeek credential."
}
if ([string]::IsNullOrWhiteSpace($env:CASEFILE_MASTER_KEY)) {
    throw "CASEFILE_MASTER_KEY is required to decrypt the saved credential."
}
if (-not (Test-Path -LiteralPath $holdoutPath -PathType Leaf)) {
    throw "Private General Mutation Holdout was not found: $holdoutPath"
}
if ((Test-Path -LiteralPath $outputPath) -and (Get-ChildItem -LiteralPath $outputPath -Force)) {
    throw "Formal output directory must be empty: $outputPath"
}

Push-Location $repoRoot
try {
    Write-Host "[M3.4-07f] Preflight: clean revision, private Holdout, exact Pro model, *_test DB"
    & uv run --project backend python -m casefile.benchmark.general_mutation_qualification `
        --repo-root $repoRoot `
        --holdout-suite $holdoutPath `
        --preflight
    if ($LASTEXITCODE -ne 0) {
        throw "General Mutation qualification preflight failed."
    }

    Write-Host "[M3.4-07f] Repository and PostgreSQL quality gates"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
    if ($LASTEXITCODE -ne 0) {
        throw "scripts/check.ps1 failed. Formal Provider trials were not started."
    }

    Write-Host "[M3.4-07f] Rechecking frozen revision after quality gates"
    & uv run --project backend python -m casefile.benchmark.general_mutation_qualification `
        --repo-root $repoRoot `
        --holdout-suite $holdoutPath `
        --preflight
    if ($LASTEXITCODE -ne 0) {
        throw "Post-check preflight failed. Formal Provider trials were not started."
    }

    Write-Host "[M3.4-07f] S0 -> 07c 40x5 -> Holdout 24x5 -> 07d 25x5 -> 07e 15x3"
    & uv run --project backend python -m casefile.benchmark.general_mutation_qualification `
        --repo-root $repoRoot `
        --holdout-suite $holdoutPath `
        --output-dir $outputPath
    if ($LASTEXITCODE -ne 0) {
        throw "General Mutation qualification did not pass; completed evidence was retained."
    }
}
finally {
    Pop-Location
}
