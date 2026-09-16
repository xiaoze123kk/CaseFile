param(
  [Parameter(Mandatory = $true)][string]$Output,
  [switch]$Live
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$arguments = @(
  "-m", "casefile.benchmark.agent_skill_cost_smoke",
  "--output", $Output,
  "--prices", (Join-Path $backend "src\casefile\benchmark\prices\deepseek-flash-20260916.json"),
  "--history-report", (Join-Path $backend "var\benchmark\prose-cost\live-flash-20260915\report.json"),
  "--history-report", (Join-Path $backend "var\benchmark\prose-cost\live-flash-projection-20260915\report.json"),
  "--history-report", (Join-Path $backend "var\benchmark\prose-skill-acceptance\live-flash-v1\report.json")
)
if ($Live) { $arguments += "--live" }
& $python @arguments
