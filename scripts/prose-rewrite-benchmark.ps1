param(
    [ValidateSet("Fake", "QualificationCheck")]
    [string]$Mode = "Fake",
    [string]$AttemptId = "local",
    [string]$QualificationSuite = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Backend virtual environment is missing. Run scripts/bootstrap.ps1 first."
}

$arguments = @("-m", "casefile.benchmark.prose_rewrite_eval")
if ($Mode -eq "Fake") {
    $outputDir = Join-Path $repoRoot "backend\var\benchmark\prose-rewrite\n4.5-b2\$AttemptId"
    $arguments += @("--mode", "fake", "--output-dir", $outputDir)
} else {
    $arguments += @("--mode", "qualification-check")
    if ($QualificationSuite) {
        $arguments += @("--qualification-suite", $QualificationSuite)
    }
}

Push-Location $repoRoot
try {
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Prose Rewrite benchmark failed."
    }
} finally {
    Pop-Location
}
