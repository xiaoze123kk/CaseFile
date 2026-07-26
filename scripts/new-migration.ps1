param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9]+(?:_[a-z0-9]+)*$')]
    [string]$Description
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

try {
    $timeZone = [TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
} catch {
    $timeZone = [TimeZoneInfo]::FindSystemTimeZoneById("Asia/Shanghai")
}

$shanghaiNow = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $timeZone)
$revision = $shanghaiNow.ToString("yyyyMMddHHmmss", [Globalization.CultureInfo]::InvariantCulture)

Push-Location $repoRoot
try {
    & $python -m alembic -c backend/alembic.ini revision --rev-id $revision -m $Description
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic failed to create migration V$revision`__$Description.py"
    }
    & (Join-Path $PSScriptRoot "check-migration-names.ps1")
} finally {
    Pop-Location
}
