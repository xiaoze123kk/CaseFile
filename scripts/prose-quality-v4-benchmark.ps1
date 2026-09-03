param(
    [ValidateSet("Fake", "QualificationCheck", "QualificationLive")]
    [string]$Mode = "Fake",
    [string]$AttemptId = "local",
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
        $output = Join-Path $repoRoot "backend\var\benchmark\prose-quality\n4.5-b3-v4-development\$AttemptId.json"
        & $python -m casefile.benchmark.prose_quality_v4_eval --mode fake --output $output
    } elseif ($Mode -eq "QualificationCheck") {
        & $python -m casefile.benchmark.prose_quality_v4_eval --mode qualification-check
    } else {
        $requiredConfirmation = "RUN_B3_V4_QUALITY_PATCH_QUALIFICATION_ONCE"
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
        $qualificationRoot = Join-Path $repoRoot "backend\var\benchmark\prose-quality\qualification-v4"
        $consumedAttempt = Get-ChildItem -LiteralPath $qualificationRoot -Directory `
            -ErrorAction SilentlyContinue | Where-Object {
                Test-Path -LiteralPath (Join-Path $_.FullName "attempt-manifest.json") -PathType Leaf
            } | Select-Object -First 1
        if ($null -ne $consumedAttempt) {
            throw "QualificationLive refuses to reuse the consumed qualification-v4 package."
        }
        $outputDir = Join-Path $qualificationRoot $AttemptId
        & $python -m casefile.benchmark.prose_quality_v4_qualification `
            --attempt-id $AttemptId `
            --output-dir $outputDir `
            --live-confirmation $requiredConfirmation
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Prose Quality v4 benchmark failed."
    }
} finally {
    Pop-Location
}
