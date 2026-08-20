$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$generatedRoot = Join-Path $repoRoot "contracts\generated"
$rootSchemaRoot = Join-Path $repoRoot "contracts\schemas"
$runtimePythonRoot = Join-Path $backendRoot "src\casefile_contracts"
$runtimeSchemaRoot = Join-Path $backendRoot "src\casefile\contracts\schemas\v2"
$legacyRuntimeSchemaRoot = Join-Path $backendRoot "src\casefile\contracts\schemas\v1"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("casefile-contracts-" + [Guid]::NewGuid().ToString("N"))
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Get-Sha256Hex {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    # Generated contract files are UTF-8 text. Git can materialize text=auto
    # files as CRLF on Windows even though the generator emits LF, so compare
    # the content with normalized line endings rather than reporting drift for
    # a checkout-only representation change.
    $content = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($content.Replace("`r`n", "`n"))
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "")
    } finally {
        $sha256.Dispose()
    }
}

function Compare-GeneratedTree {
    param(
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Actual
    )

    $expectedFiles = @{}
    Get-ChildItem -LiteralPath $Expected -Recurse -File |
        Where-Object { $_.Extension -ne ".pyc" -and $_.FullName -notmatch "\\__pycache__\\" } |
        ForEach-Object {
        $relative = $_.FullName.Substring($Expected.Length).TrimStart('\')
        $expectedFiles[$relative] = Get-Sha256Hex -Path $_.FullName
    }

    $actualFiles = @{}
    Get-ChildItem -LiteralPath $Actual -Recurse -File |
        Where-Object { $_.Extension -ne ".pyc" -and $_.FullName -notmatch "\\__pycache__\\" } |
        ForEach-Object {
        $relative = $_.FullName.Substring($Actual.Length).TrimStart('\')
        $actualFiles[$relative] = Get-Sha256Hex -Path $_.FullName
    }

    $allFiles = @($expectedFiles.Keys + $actualFiles.Keys | Sort-Object -Unique)
    $differences = @()
    foreach ($relative in $allFiles) {
        if (-not $expectedFiles.ContainsKey($relative)) {
            $differences += "unexpected generated file: $relative"
        } elseif (-not $actualFiles.ContainsKey($relative)) {
            $differences += "missing generated file: $relative"
        } elseif ($expectedFiles[$relative] -ne $actualFiles[$relative]) {
            $differences += "generated file differs: $relative"
        }
    }

    if ($differences.Count -gt 0) {
        throw "Generated contracts are stale or hand-edited:`n$($differences -join "`n")"
    }
}

$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

Push-Location $repoRoot
try {
    Write-Host "Checking contract dependencies..."
    & $python -c "import datamodel_code_generator, jsonschema, pydantic"
    if ($LASTEXITCODE -ne 0) {
        throw "Python contract dependencies are missing. Run uv sync --project backend --all-groups."
    }

    Write-Host "Checking generated contract drift..."
    & (Join-Path $PSScriptRoot "generate-contracts.ps1") -OutputRoot $temporaryRoot
    Compare-GeneratedTree -Expected $generatedRoot -Actual $temporaryRoot
    Compare-GeneratedTree `
        -Expected (Join-Path $temporaryRoot "python\src\casefile_contracts") `
        -Actual $runtimePythonRoot

    $temporaryRuntimeSchemaRoot = Join-Path $temporaryRoot "runtime-schemas"
    New-Item -ItemType Directory -Path $temporaryRuntimeSchemaRoot -Force | Out-Null
    foreach ($schemaDirectory in @("brief", "brief-intake", "casefile", "task", "validation")) {
        Copy-Item -LiteralPath (Join-Path $rootSchemaRoot $schemaDirectory) `
            -Destination $temporaryRuntimeSchemaRoot -Recurse
    }
    Copy-Item -LiteralPath (Join-Path $rootSchemaRoot "editing-contracts.schema.json") `
        -Destination $temporaryRuntimeSchemaRoot
    [System.IO.File]::WriteAllText(
        (Join-Path $temporaryRuntimeSchemaRoot "GENERATED_FROM_ROOT_SCHEMAS.txt"),
        "Generated from current v2 contracts/schemas by scripts/generate-contracts.ps1; do not edit by hand. The adjacent v1 mirror is retained for historical reads.`n",
        $utf8NoBom
    )
    Compare-GeneratedTree -Expected $temporaryRuntimeSchemaRoot -Actual $runtimeSchemaRoot
    foreach ($legacySchema in @("casefile.schema.json", "common.schema.json", "objects.schema.json")) {
        $legacyPath = Join-Path $legacyRuntimeSchemaRoot "casefile\$legacySchema"
        if (-not (Test-Path -LiteralPath $legacyPath -PathType Leaf)) {
            throw "Historical v1 runtime schema is missing: $legacyPath"
        }
    }

    Write-Host "Running Python contract tests..."
    & $python -m pytest backend/tests/contract
    if ($LASTEXITCODE -ne 0) {
        throw "Python contract tests failed."
    }

    Write-Host "Compiling TypeScript contract consumers..."
    & pnpm exec tsc --project contracts/tests/tsconfig.json
    if ($LASTEXITCODE -ne 0) {
        throw "TypeScript contract compilation failed."
    }

    Write-Host "Running TypeScript contract roundtrip tests..."
    & pnpm exec tsx contracts/tests/contract-roundtrip.ts
    if ($LASTEXITCODE -ne 0) {
        throw "TypeScript contract tests failed."
    }

    Write-Host "Contract checks passed."
} finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
    Pop-Location
}
