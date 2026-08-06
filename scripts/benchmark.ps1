param(
    [ValidateSet("fake", "live")][string]$Mode = "fake",
    [ValidateRange(1, 100)][int]$Repeats = 3,
    [string]$Model = "gpt-5.6-sol",
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
    if ($SuiteDir) {
        $suitePath = if ([System.IO.Path]::IsPathRooted($SuiteDir)) {
            $SuiteDir
        } else {
            Join-Path $repoRoot $SuiteDir
        }
        & $python -m casefile.benchmark `
            --suite $suitePath `
            --mode $Mode `
            --repeats $Repeats `
            --model $Model
    } else {
        $fixturePath = if ([System.IO.Path]::IsPathRooted($Fixture)) {
            $Fixture
        } else {
            Join-Path $repoRoot $Fixture
        }
        & $python -m casefile.benchmark `
            --fixture $fixturePath `
            --mode $Mode `
            --repeats $Repeats `
            --model $Model
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Benchmark failed."
    }
} finally {
    Pop-Location
}
