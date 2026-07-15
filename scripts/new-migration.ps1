param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9]+(?:_[a-z0-9]+)*$')]
    [string]$Name
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$migrationDirectory = Join-Path $backendRoot "migrations\versions"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

$chinaTime = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
    [datetime]::UtcNow,
    'China Standard Time'
)
$candidate = $chinaTime
$existingVersions = @(
    Get-ChildItem -LiteralPath $migrationDirectory -File -Filter 'V*.py' |
        ForEach-Object {
            if ($_.Name -match '^V([0-9]{14})__') { $Matches[1] }
        }
)

do {
    $revision = $candidate.ToString('yyyyMMddHHmmss')
    $candidate = $candidate.AddSeconds(1)
} while ($existingVersions -contains $revision)

Push-Location $backendRoot
try {
    & $python -m alembic revision --rev-id $revision --message $Name
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic failed to create migration $revision."
    }
} finally {
    Pop-Location
}

& (Join-Path $PSScriptRoot "check-migration-names.ps1")
