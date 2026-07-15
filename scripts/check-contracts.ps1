$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$generatedRoot = Join-Path $repoRoot "contracts\generated"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("casefile-contracts-" + [Guid]::NewGuid().ToString("N"))

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
        $expectedFiles[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }

    $actualFiles = @{}
    Get-ChildItem -LiteralPath $Actual -Recurse -File |
        Where-Object { $_.Extension -ne ".pyc" -and $_.FullName -notmatch "\\__pycache__\\" } |
        ForEach-Object {
        $relative = $_.FullName.Substring($Actual.Length).TrimStart('\')
        $actualFiles[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
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
