param(
    [ValidateSet("Fake", "DevelopmentLive", "ProRescoreLive", "QualificationCheck", "QualificationLive")]
    [string]$Mode = "Fake",
    [string]$AttemptId = "local",
    [string]$QualificationSuite = "",
    [string]$SourceAttempt = "",
    [string]$LiveConfirmation = "",
    [string]$DiagnosticSuite = "fixtures/prose_quality_benchmark/diagnostic_v1/suite.json",
    [ValidateSet("both", "v2-flash", "diagnostic-pro-pairwise-v1")]
    [string]$Candidates = "both",
    [ValidateSet(3)]
    [int]$Repeats = 3,
    [ValidateRange(1, 4)]
    [int]$Workers = 4
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
    } elseif ($Mode -eq "DevelopmentLive") {
        & $python -m casefile.benchmark.prose_quality_diagnostic --suite $DiagnosticSuite --attempt-id $AttemptId --candidates $Candidates --repeats $Repeats --workers $Workers
    } elseif ($Mode -eq "ProRescoreLive") {
        if ([string]::IsNullOrWhiteSpace($SourceAttempt)) {
            throw "ProRescoreLive requires -SourceAttempt pointing to a completed public diagnostic attempt."
        }
        & $python -m casefile.benchmark.prose_quality_rescore --source-attempt $SourceAttempt --attempt-id $AttemptId --workers $Workers
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
        $qualificationRoot = Join-Path $repoRoot "backend\var\benchmark\prose-quality\qualification-v2"
        $consumedAttempt = Get-ChildItem -LiteralPath $qualificationRoot -Directory `
            -ErrorAction SilentlyContinue | Where-Object {
                Test-Path -LiteralPath (Join-Path $_.FullName "attempt-manifest.json") -PathType Leaf
            } | Select-Object -First 1
        if ($null -ne $consumedAttempt) {
            throw (
                "QualificationLive refuses to reuse the consumed qualification-v2 package. " +
                "Freeze a new private package before another formal attempt."
            )
        }
        $outputDir = Join-Path $qualificationRoot $AttemptId
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
