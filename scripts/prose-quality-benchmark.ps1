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

Push-Location $repoRoot
try {
    if ($Mode -eq "Fake") {
        $outputDir = Join-Path $repoRoot "backend\var\benchmark\prose-quality\n4.5-b3-development\$AttemptId"
        & $python -m casefile.benchmark.prose_quality_eval --mode fake --output-dir $outputDir
    } elseif ($Mode -eq "QualificationCheck") {
        $arguments = @("-m", "casefile.benchmark.prose_quality_eval", "--mode", "qualification-check")
        if ($QualificationSuite) {
            $arguments += @("--qualification-suite", $QualificationSuite)
        }
        & $python @arguments
    } else {
        $requiredConfirmation = "RUN_B3_QUALITY_POLISHER_QUALIFICATION_ONCE"
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
        $outputDir = Join-Path $repoRoot "backend\var\benchmark\prose-quality\qualification-v2\$AttemptId"
        $arguments = @(
            "-m", "casefile.benchmark.prose_quality_qualification",
            "--attempt-id", $AttemptId,
            "--output-dir", $outputDir,
            "--live-confirmation", $requiredConfirmation
        )
        if ($QualificationSuite) {
            $arguments += @("--qualification-suite", $QualificationSuite)
        }
        & $python @arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Prose Quality development benchmark failed."
    }
} finally {
    Pop-Location
}
