[CmdletBinding()]
param(
    [ValidateSet("Fake")]
    [string]$Mode = "Fake",
    [string]$AttemptId = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"

if ([string]::IsNullOrWhiteSpace($AttemptId)) {
    $AttemptId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
}
$outputDir = Join-Path $backendRoot "var/benchmark/prose-writer/n4.5-04/$AttemptId"

Push-Location $backendRoot
try {
    & uv run python -m casefile.benchmark.prose_writer_eval `
        --mode $Mode.ToLowerInvariant() `
        --output-dir $outputDir
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
