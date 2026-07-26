param(
    [string]$MigrationDirectory = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($MigrationDirectory)) {
    $MigrationDirectory = Join-Path $repoRoot "backend\migrations\versions"
}

if (-not (Test-Path -LiteralPath $MigrationDirectory -PathType Container)) {
    throw "Migration directory does not exist: $MigrationDirectory"
}

$namePattern = '^V(?<version>[0-9]{14})__(?<description>[a-z0-9_]+)\.py$'
$revisionPattern = '(?m)^revision(?:\s*:\s*[^=]+)?\s*=\s*["''](?<value>[0-9]{14})["'']\s*$'
$downRevisionPattern = '(?m)^down_revision(?:\s*:\s*[^=]+)?\s*=\s*(?<value>None|["''][0-9]{14}["''])\s*$'
$errors = [System.Collections.Generic.List[string]]::new()
$entries = [System.Collections.Generic.List[object]]::new()
$seenVersions = @{}

$files = @(
    Get-ChildItem -LiteralPath $MigrationDirectory -File -Filter '*.py' |
        Where-Object { $_.Name -ne '__init__.py' } |
        Sort-Object Name
)
if ($files.Count -eq 0) {
    throw "No Alembic migrations found in $MigrationDirectory"
}

foreach ($file in $files) {
    $nameMatch = [regex]::Match($file.Name, $namePattern)
    if (-not $nameMatch.Success) {
        $errors.Add(
            "Invalid migration name: $($file.Name). " +
            "Expected VyyyyMMddHHmmss__lower_snake_case.py"
        )
        continue
    }

    $version = $nameMatch.Groups['version'].Value
    $description = $nameMatch.Groups['description'].Value
    if ($description -match '__' -or $description.StartsWith('_') -or $description.EndsWith('_')) {
        $errors.Add("Invalid migration description: $($file.Name)")
    }

    $parsedTimestamp = [datetime]::MinValue
    $isRealTimestamp = [datetime]::TryParseExact(
        $version,
        'yyyyMMddHHmmss',
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::None,
        [ref]$parsedTimestamp
    )
    if (-not $isRealTimestamp) {
        $errors.Add("Migration version is not a real timestamp: $($file.Name)")
    }

    if ($seenVersions.ContainsKey($version)) {
        $errors.Add("Duplicate migration version $version in $($seenVersions[$version]) and $($file.Name)")
    } else {
        $seenVersions[$version] = $file.Name
    }

    $content = Get-Content -LiteralPath $file.FullName -Raw
    $revisionMatch = [regex]::Match($content, $revisionPattern)
    if (-not $revisionMatch.Success) {
        $errors.Add("Missing timestamp revision declaration: $($file.Name)")
        continue
    }
    if ($revisionMatch.Groups['value'].Value -ne $version) {
        $errors.Add("Filename and revision differ: $($file.Name)")
    }

    $downRevisionMatch = [regex]::Match($content, $downRevisionPattern)
    if (-not $downRevisionMatch.Success) {
        $errors.Add("Missing down_revision declaration: $($file.Name)")
        continue
    }
    $downRevision = $downRevisionMatch.Groups['value'].Value.Trim('"', "'")

    $entries.Add([pscustomobject]@{
        File = $file.Name
        Version = $version
        DownRevision = $downRevision
    })
}

$orderedEntries = @($entries | Sort-Object Version)
for ($index = 0; $index -lt $orderedEntries.Count; $index++) {
    $entry = $orderedEntries[$index]
    $expectedDownRevision = if ($index -eq 0) { 'None' } else { $orderedEntries[$index - 1].Version }
    if ($entry.DownRevision -ne $expectedDownRevision) {
        $errors.Add(
            "Invalid chain at $($entry.File): " +
            "down_revision=$($entry.DownRevision), expected $expectedDownRevision"
        )
    }
}

if ($errors.Count -gt 0) {
    throw ($errors -join [Environment]::NewLine)
}

Write-Host "Alembic migration names and chain are valid ($($orderedEntries.Count) revisions)."
