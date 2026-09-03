param(
    [string]$AttemptId = "local"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Backend virtual environment is missing. Run scripts/bootstrap.ps1 first."
}

$outputDir = Join-Path $repoRoot "backend\var\benchmark\prose-quality\n4.5-b3-development\$AttemptId"
Push-Location $repoRoot
try {
    & $python -m casefile.benchmark.prose_quality_eval --output-dir $outputDir
    if ($LASTEXITCODE -ne 0) {
        throw "Prose Quality development benchmark failed."
    }
} finally {
    Pop-Location
}
