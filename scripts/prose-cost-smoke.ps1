[CmdletBinding()]
param(
    [ValidateSet("Fake", "Live")][string]$Mode = "Fake",
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$Prices = "",
    [decimal]$BudgetCny = 20
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend/.venv/Scripts/python.exe"
$arguments = @("-m", "casefile.benchmark.prose_cost_smoke", "--mode", $Mode.ToLowerInvariant(),
    "--output-dir", $OutputDir, "--budget-cny", $BudgetCny.ToString([cultureinfo]::InvariantCulture))
if ($Prices) { $arguments += @("--prices", $Prices) }
& $python @arguments
exit $LASTEXITCODE
