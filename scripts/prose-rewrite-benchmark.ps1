param(
    [ValidateSet("Fake", "QualificationCheck", "QualificationLive")]
    [string]$Mode = "Fake",
    [string]$AttemptId = "local",
    [string]$QualificationSuite = "",
    [string]$LiveConfirmation = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Backend virtual environment is missing. Run scripts/bootstrap.ps1 first."
}

if ($Mode -eq "Fake") {
    $arguments = @("-m", "casefile.benchmark.prose_rewrite_eval")
    $outputDir = Join-Path $repoRoot "backend\var\benchmark\prose-rewrite\n4.5-b2\$AttemptId"
    $arguments += @("--mode", "fake", "--output-dir", $outputDir)
} elseif ($Mode -eq "QualificationCheck") {
    $arguments = @("-m", "casefile.benchmark.prose_rewrite_eval")
    $arguments += @("--mode", "qualification-check")
    if ($QualificationSuite) {
        $arguments += @("--qualification-suite", $QualificationSuite)
    }
} else {
    $requiredConfirmation = "RUN_B2_REWRITE_QUALIFICATION_ONCE"
    if ($LiveConfirmation -cne $requiredConfirmation) {
        throw "QualificationLive requires -LiveConfirmation $requiredConfirmation"
    }
    if ($AttemptId -eq "local") {
        throw "QualificationLive requires an explicit unique -AttemptId."
    }
    $configured = -not [string]::IsNullOrWhiteSpace($env:CASEFILE_DEEPSEEK_API_KEY) -or
        -not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)
    if (-not $configured) {
        throw "QualificationLive requires CASEFILE_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY."
    }
    $outputDir = Join-Path $repoRoot "backend\var\benchmark\prose-rewrite\qualification-v1\$AttemptId"
    $arguments = @(
        "-m", "casefile.benchmark.prose_rewrite_qualification",
        "--attempt-id", $AttemptId,
        "--output-dir", $outputDir,
        "--live-confirmation", $requiredConfirmation
    )
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
