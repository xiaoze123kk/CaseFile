param(
    [string]$HoldoutSuite = "backend/var/benchmark/private/chat-goal-interactive-v1/suite.json",
    [string]$DatabaseUrl,
    [string]$CredentialDatabaseUrl,
    [string]$OutputDirectory,
    [switch]$Preflight
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend/.venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

function Import-LocalSetting([string]$Name) {
    $current = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        return $current
    }
    $envFile = Join-Path $repoRoot ".env"
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        return $null
    }
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match "^$([regex]::Escape($Name))=(.*)$") {
            $value = $Matches[1].Trim()
            if (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            ) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $null
}

if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    $DatabaseUrl = Import-LocalSetting "CASEFILE_TEST_DATABASE_URL"
}
if ([string]::IsNullOrWhiteSpace($CredentialDatabaseUrl)) {
    $CredentialDatabaseUrl = Import-LocalSetting "DATABASE_URL"
}
if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    throw "CASEFILE_TEST_DATABASE_URL is required for M3.8-07 qualification."
}
if ([string]::IsNullOrWhiteSpace($CredentialDatabaseUrl)) {
    throw "DATABASE_URL is required for the local Provider binding."
}

$arguments = @(
    "-m", "casefile.benchmark.chat_goal_interactive_qualification",
    "--repo-root", $repoRoot,
    "--holdout-suite", (Join-Path $repoRoot $HoldoutSuite),
    "--database-url", $DatabaseUrl,
    "--credential-database-url", $CredentialDatabaseUrl
)
if ($Preflight) {
    $arguments += "--preflight"
} else {
    if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
        $shortRevision = (& git -C $repoRoot rev-parse --short HEAD).Trim()
        $OutputDirectory = "backend/var/benchmark/m3.8-07-formal/$shortRevision"
    }
    $arguments += @("--output-dir", (Join-Path $repoRoot $OutputDirectory))
}

Push-Location $repoRoot
try {
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "M3.8-07 qualification did not pass. Completed evidence was retained."
    }
} finally {
    Pop-Location
}
