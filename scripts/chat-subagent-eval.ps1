param(
    [Parameter(Mandatory = $true)][string]$DatabaseUrl,
    [Parameter(Mandatory = $true)][string]$Prices,
    [Parameter(Mandatory = $true)][decimal]$BudgetCny,
    [Parameter(Mandatory = $true)][int]$MaxInputTokensPerTrial,
    [Parameter(Mandatory = $true)][int]$MaxOutputTokensPerTrial,
    [string]$OutputDir = "backend/var/benchmark/chat-subagents-v1",
    [int]$ActorId = 1
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $repoRoot "backend")
try {
    uv run --extra dev python -m casefile.benchmark.chat_subagent_live_eval `
        --output-dir (Join-Path $repoRoot $OutputDir) `
        --prices (Join-Path $repoRoot $Prices) `
        --budget-cny $BudgetCny `
        --database-url $DatabaseUrl `
        --actor-id $ActorId `
        --max-input-tokens-per-trial $MaxInputTokensPerTrial `
        --max-output-tokens-per-trial $MaxOutputTokensPerTrial
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
