param(
    [ValidateSet("Fake", "QualificationCheck")]
    [string]$Mode = "Fake",
    [string]$AttemptId = "local"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Backend virtual environment is missing. Run scripts/bootstrap.ps1 first."
}

Push-Location $repoRoot
try {
    if ($Mode -eq "QualificationCheck") {
        & $python -m casefile.benchmark.prose_quality_eval --mode qualification-check
    } else {
        $outputDir = Join-Path $repoRoot "backend\var\benchmark\prose-quality\n4.5-b3-development\$AttemptId"
        & $python -m casefile.benchmark.prose_quality_eval --mode fake --output-dir $outputDir
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Prose Quality development benchmark failed."
    }
} finally {
    Pop-Location
}
