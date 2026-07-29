param(
    [string]$OutputRoot
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$schemaRoot = Join-Path $repoRoot "contracts\schemas"
$schemaEntry = Join-Path $schemaRoot "editing-contracts.schema.json"
$runtimeSchemaRoot = Join-Path $backendRoot "src\casefile\contracts\schemas\v1"
$runtimePythonRoot = Join-Path $backendRoot "src\casefile_contracts"
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
$pythonGenerationPackage = Join-Path $pythonRoot "src\casefile_contracts_generated"
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

New-Item -ItemType Directory -Path (Split-Path -Parent $pythonPackage) -Force | Out-Null
New-Item -ItemType Directory -Path $typescriptRoot -Force | Out-Null

$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

$generatorJson = & $python -m datamodel_code_generator `
    --input $schemaEntry `
    --input-file-type jsonschema `
    --output $pythonGenerationPackage `
    --output-model-type pydantic_v2.BaseModel `
    --preset standard-py312-20260619 `
    --ignore-pyproject `
    --strict-refs `
    --extra-fields forbid `
    --disable-timestamp `
    --formatters builtin `
    --output-format json
$generatorExitCode = $LASTEXITCODE
if ($generatorExitCode -ne 0) {
    throw "Python contract generation failed."
}
$generatorReport = ($generatorJson -join "`n") | ConvertFrom-Json
$generatedFiles = @($generatorReport.files)
if ($generatedFiles.Count -eq 0) {
    throw "Python contract generation returned no files."
}
foreach ($generatedFile in $generatedFiles) {
    $generatedPath = Join-Path $pythonGenerationPackage $generatedFile.path
    if (-not (Test-Path -LiteralPath $generatedPath -PathType Leaf)) {
        throw "Python contract generation did not materialize $($generatedFile.path)."
    }
}
Move-Item -LiteralPath $pythonGenerationPackage -Destination $pythonPackage

foreach ($generatedPythonFile in Get-ChildItem -LiteralPath $pythonPackage -Filter "*.py") {
    $generatedContent = [System.IO.File]::ReadAllText($generatedPythonFile.FullName)
    Write-GeneratedFile `
        -Path $generatedPythonFile.FullName `
        -Content ("# ruff: noqa: E501, I001`n" + $generatedContent)
}

$publicModule = @'
# generated from contracts/schemas; DO NOT EDIT BY HAND.

from ._internal import AgentGenerateRequest, AgentGenerateResult, TaskEvent, TaskRun
from .brief import Schema as Brief
from .casefile import Schema as CaseFile
from .patch_candidate import Schema as PatchCandidate
from .validation_issue import Schema as ValidationIssue

__all__ = [
    "AgentGenerateRequest",
    "AgentGenerateResult",
    "Brief",
    "CaseFile",
    "PatchCandidate",
    "TaskEvent",
    "TaskRun",
    "ValidationIssue",
]
'@
Write-GeneratedFile -Path (Join-Path $pythonPackage "public.py") -Content $publicModule

$initPath = Join-Path $pythonPackage "__init__.py"
$initContent = [System.IO.File]::ReadAllText($initPath)
$initContent += @'

from .public import (
    Brief,
    CaseFile,
    PatchCandidate,
    ValidationIssue,
)

__all__ += ["Brief", "CaseFile", "PatchCandidate", "ValidationIssue"]
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

$typescriptOutput = Join-Path $typescriptRoot "index.d.ts"
& pnpm exec json2ts `
    --cwd $schemaRoot `
    --input $schemaEntry `
    --output $typescriptOutput
if ($LASTEXITCODE -ne 0) {
    throw "TypeScript contract generation failed."
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $runtimeSchemaFullPath = [System.IO.Path]::GetFullPath($runtimeSchemaRoot)
    $backendSourceFullPath = [System.IO.Path]::GetFullPath(
        (Join-Path $backendRoot "src\casefile\contracts\schemas")
    ).TrimEnd('\')
    if (-not $runtimeSchemaFullPath.StartsWith(
        $backendSourceFullPath + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to replace runtime schemas outside the backend contract package."
    }
    if (Test-Path -LiteralPath $runtimeSchemaFullPath) {
        Remove-Item -LiteralPath $runtimeSchemaFullPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $runtimeSchemaFullPath -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $schemaRoot "casefile") `
        -Destination $runtimeSchemaFullPath -Recurse
    Write-GeneratedFile `
        -Path (Join-Path $runtimeSchemaFullPath "GENERATED_FROM_ROOT_SCHEMAS.txt") `
        -Content "Generated from contracts/schemas by scripts/generate-contracts.ps1; do not edit by hand.`n"

    $runtimePythonFullPath = [System.IO.Path]::GetFullPath($runtimePythonRoot)
    $backendSourceRoot = [System.IO.Path]::GetFullPath((Join-Path $backendRoot "src")).TrimEnd('\')
    if (-not $runtimePythonFullPath.StartsWith(
        $backendSourceRoot + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to replace runtime Python contracts outside backend/src."
    }
    if (Test-Path -LiteralPath $runtimePythonFullPath) {
        Remove-Item -LiteralPath $runtimePythonFullPath -Recurse -Force
    }
    Copy-Item -LiteralPath $pythonPackage -Destination $runtimePythonFullPath -Recurse
}

Write-Host "Generated Python and TypeScript contracts under $outputFullPath"
