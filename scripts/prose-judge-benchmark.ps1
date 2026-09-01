[CmdletBinding()]
param(
    [ValidateSet("Fake", "Live", "Smoke")]
    [string]$Mode = "Fake",
    [string]$AttemptId = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"

if ($Mode -ne "Fake") {
    $configured = -not [string]::IsNullOrWhiteSpace($env:CASEFILE_DEEPSEEK_API_KEY) -or
        -not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)
    if (-not $configured) {
        $envPath = Join-Path $repoRoot ".env"
        if (Test-Path -LiteralPath $envPath) {
            foreach ($line in Get-Content -LiteralPath $envPath) {
                if ($line -match '^\s*(CASEFILE_DEEPSEEK_API_KEY|DEEPSEEK_API_KEY)\s*=\s*(.+?)\s*$') {
                    $name = $Matches[1]
                    $value = $Matches[2].Trim().Trim('"').Trim("'")
                    if (-not [string]::IsNullOrWhiteSpace($value)) {
                        [Environment]::SetEnvironmentVariable($name, $value, "Process")
                    }
                }
            }
        }
    }
    $configured = -not [string]::IsNullOrWhiteSpace($env:CASEFILE_DEEPSEEK_API_KEY) -or
        -not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)
    if (-not $configured) {
        throw "DeepSeek API key is not configured in the process environment or repository .env."
    }
}

if ([string]::IsNullOrWhiteSpace($AttemptId)) {
    $AttemptId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
}
$phaseDirectory = if ($Mode -eq "Smoke") { "n4.5-02r" } else { "n4.5-02" }
$outputDir = Join-Path $backendRoot "var/benchmark/prose-judge/$phaseDirectory/$AttemptId"
$modeValue = $Mode.ToLowerInvariant()

Push-Location $backendRoot
try {
    $arguments = @(
        "run", "python", "-m", "casefile.benchmark.prose_judge_eval",
        "--mode", $modeValue,
        "--output-dir", $outputDir
    )
    if ($Mode -eq "Live") {
        $arguments += "--freeze-repo"
    }
    & uv @arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
