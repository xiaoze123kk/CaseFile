param(
    [ValidateSet("fake", "live")][string]$Mode = "fake",
    [ValidateRange(1, 100)][int]$Repeats = 3,
    [string]$Model = "gpt-5.6-sol",
    [ValidateSet("openai", "deepseek")][string]$Provider = "openai",
    [string]$PromptVersion = "",
    [string]$ReportPath = "",
    [string]$Fixture = "fixtures\benchmark\brief_to_draft.json",
    [string]$SuiteDir = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

Push-Location (Join-Path $repoRoot "backend")
try {
    $arguments = @("-m", "casefile.benchmark", "--mode", $Mode, "--repeats", $Repeats, "--model", $Model, "--provider", $Provider)
    if ($SuiteDir) {
        $suitePath = if ([System.IO.Path]::IsPathRooted($SuiteDir)) {
            $SuiteDir
        } else {
            Join-Path $repoRoot $SuiteDir
        }
        $arguments += @("--suite", $suitePath)
    } else {
        $fixturePath = if ([System.IO.Path]::IsPathRooted($Fixture)) {
            $Fixture
        } else {
            Join-Path $repoRoot $Fixture
        }
        $arguments += @("--fixture", $fixturePath)
    }
    if ($PromptVersion) {
        $arguments += @("--prompt-version", $PromptVersion)
    }
    if ($ReportPath) {
        $reportFile = if ([System.IO.Path]::IsPathRooted($ReportPath)) {
            $ReportPath
        } else {
            Join-Path $repoRoot $ReportPath
        }
        $arguments += @("--report-path", $reportFile)
    }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Benchmark failed."
    }
} finally {
    Pop-Location
}
