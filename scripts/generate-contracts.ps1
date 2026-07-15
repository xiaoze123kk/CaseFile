param(
    [string]$OutputRoot
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$schemaRoot = Join-Path $repoRoot "contracts\schemas"
$schemaEntry = Join-Path $schemaRoot "editing-contracts.schema.json"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-GeneratedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )

    $normalized = $Content.Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($Path, $normalized, $utf8NoBom)
}

$resolvedOutputRoot = if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    Join-Path $repoRoot "contracts\generated"
} elseif ([System.IO.Path]::IsPathRooted($OutputRoot)) {
    [System.IO.Path]::GetFullPath($OutputRoot)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputRoot))
}

$repoFullPath = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
$outputFullPath = [System.IO.Path]::GetFullPath($resolvedOutputRoot).TrimEnd('\')
if ($outputFullPath -eq $repoFullPath) {
    throw "Contract output root cannot be the repository root."
}

$pythonRoot = Join-Path $outputFullPath "python"
$pythonPackage = Join-Path $pythonRoot "src\casefile_contracts"
$typescriptRoot = Join-Path $outputFullPath "typescript"

foreach ($target in @($pythonRoot, $typescriptRoot)) {
    $targetFullPath = [System.IO.Path]::GetFullPath($target)
    if (-not $targetFullPath.StartsWith($outputFullPath + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace contract output outside the requested root: $targetFullPath"
    }
    if (Test-Path -LiteralPath $targetFullPath) {
        Remove-Item -LiteralPath $targetFullPath -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $pythonPackage -Force | Out-Null
New-Item -ItemType Directory -Path $typescriptRoot -Force | Out-Null

$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

& $python -m datamodel_code_generator `
    --input $schemaEntry `
    --input-file-type jsonschema `
    --output $pythonPackage `
    --output-model-type pydantic_v2.BaseModel `
    --preset standard-py312-20260619 `
    --extra-fields forbid `
    --disable-timestamp `
    --formatters builtin
if ($LASTEXITCODE -ne 0) {
    throw "Python contract generation failed."
}

$publicModule = @'
# generated from contracts/schemas; DO NOT EDIT BY HAND.

from .casefile import Schema as CaseFile
from .patch_candidate import Schema as PatchCandidate
from .validation_issue import Schema as ValidationIssue

__all__ = ["CaseFile", "PatchCandidate", "ValidationIssue"]
'@
Write-GeneratedFile -Path (Join-Path $pythonPackage "public.py") -Content $publicModule

$initPath = Join-Path $pythonPackage "__init__.py"
$initContent = [System.IO.File]::ReadAllText($initPath)
$initContent += @'

from .public import CaseFile, PatchCandidate, ValidationIssue

__all__ += ["CaseFile", "PatchCandidate", "ValidationIssue"]
'@
Write-GeneratedFile -Path $initPath -Content $initContent
Write-GeneratedFile -Path (Join-Path $pythonPackage "py.typed") -Content ""

$pythonPackageMetadata = @'
[project]
name = "casefile-contracts"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.9,<3"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/casefile_contracts"]
'@
Write-GeneratedFile -Path (Join-Path $pythonRoot "pyproject.toml") -Content $pythonPackageMetadata

$typescriptOutput = Join-Path $typescriptRoot "index.d.ts"
& pnpm exec json2ts `
    --cwd $schemaRoot `
    --input $schemaEntry `
    --output $typescriptOutput
if ($LASTEXITCODE -ne 0) {
    throw "TypeScript contract generation failed."
}

$typescriptPackageMetadata = @'
{
  "name": "@casefile/contracts",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "types": "./index.d.ts",
  "exports": {
    ".": {
      "types": "./index.d.ts"
    }
  },
  "files": [
    "index.d.ts"
  ]
}
'@
Write-GeneratedFile -Path (Join-Path $typescriptRoot "package.json") -Content $typescriptPackageMetadata

Write-Host "Generated Python and TypeScript contracts under $outputFullPath"
