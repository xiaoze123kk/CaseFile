[CmdletBinding()]
param(
    [switch]$SkipDependencySync,
    [int]$ApiPort = 8000,
    [int]$WebPort = 3000
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$runDir = Join-Path $repoRoot "var\dev"
$nodeBin = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
$pnpmFallback = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"

function Add-PathEntry([string]$path) {
    if ((Test-Path -LiteralPath $path -PathType Container) -and
        -not (($env:Path -split ";") -contains $path)) {
        $env:Path = "$path;$env:Path"
    }
}

function Wait-HttpReady([string]$uri, [int]$timeoutSeconds = 90) {
    $deadline = [DateTimeOffset]::Now.AddSeconds($timeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                return $response
            }
        } catch {
            # The process may still be starting.
        }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::Now -lt $deadline)
    throw "Timed out waiting for $uri."
}

function Stop-PortOwner([int]$port) {
    $owners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -eq $port } |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($owner in $owners) {
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
    }
}

function Stop-CaseFileWorkers {
    $workers = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*-m casefile.worker*" }
    foreach ($worker in $workers) {
        Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Show-LogTail([string]$path) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        Write-Host "--- $path ---"
        Get-Content -LiteralPath $path -Tail 20
    }
}

function Test-DockerEngine {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & docker info *> $null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

Push-Location $repoRoot
try {
    Add-PathEntry $nodeBin
    Add-PathEntry (Split-Path -Parent $pnpmFallback)

    $pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $pnpm -and (Test-Path -LiteralPath $pnpmFallback -PathType Leaf)) {
        $pnpm = Get-Command $pnpmFallback -ErrorAction Stop
    }
    if ($null -eq $pnpm) {
        throw "pnpm was not found. Install Node.js/pnpm or set PATH before running this script."
    }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Backend virtual environment is missing: $venvPython"
    }

    if (-not (Test-DockerEngine)) {
        $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        if (-not (Test-Path -LiteralPath $dockerDesktop -PathType Leaf)) {
            throw "Docker Engine is unavailable and Docker Desktop was not found."
        }
        Write-Host "Starting Docker Desktop..."
        Start-Process -FilePath $dockerDesktop -WindowStyle Hidden | Out-Null
        $dockerDeadline = [DateTimeOffset]::Now.AddSeconds(90)
        do {
            Start-Sleep -Seconds 2
        } while (-not (Test-DockerEngine) -and [DateTimeOffset]::Now -lt $dockerDeadline)
        if (-not (Test-DockerEngine)) {
            throw "Docker Engine did not become ready within 90 seconds."
        }
    }

    if (-not $SkipDependencySync) {
        Write-Host "Syncing workspace dependencies..."
        & $pnpm.Source install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) {
            throw "pnpm dependency sync failed."
        }
        & $venvPython -m pip install -e ".\backend" --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "Backend dependency sync failed."
        }
    }

    Write-Host "Preparing PostgreSQL and applying migrations..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "bootstrap.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Database bootstrap failed."
    }

    if (Test-Path -LiteralPath ".env" -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath ".env" -Encoding utf8) {
            $trimmed = $line.Trim()
            if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
                continue
            }
            $parts = $trimmed.Split("=", 2)
            if ($parts.Count -eq 2) {
                Set-Item -Path ("Env:" + $parts[0]) -Value $parts[1]
            }
        }
    }

    Stop-PortOwner $ApiPort
    Stop-PortOwner $WebPort
    Stop-CaseFileWorkers
    Start-Sleep -Milliseconds 300

    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $apiOut = Join-Path $runDir "api-$stamp.out.log"
    $apiErr = Join-Path $runDir "api-$stamp.err.log"
    $webOut = Join-Path $runDir "web-$stamp.out.log"
    $webErr = Join-Path $runDir "web-$stamp.err.log"
    $workerOut = Join-Path $runDir "worker-$stamp.out.log"
    $workerErr = Join-Path $runDir "worker-$stamp.err.log"

    $api = Start-Process -FilePath $venvPython `
        -ArgumentList @("-m", "uvicorn", "casefile.api.app:app", "--host", "127.0.0.1", "--port", "$ApiPort") `
        -WorkingDirectory $backendRoot -WindowStyle Hidden `
        -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr -PassThru
    # Start pnpm through cmd.exe so .cmd shims work reliably.
    # Pass --port explicitly so an external PORT value cannot override -WebPort.
    $web = Start-Process -FilePath $env:ComSpec `
        -ArgumentList @("/c", "`"$($pnpm.Source)`" --filter @casefile/web dev --port $WebPort") `
        -WorkingDirectory $repoRoot -WindowStyle Hidden `
        -RedirectStandardOutput $webOut -RedirectStandardError $webErr -PassThru
    if ([string]::IsNullOrWhiteSpace($env:CASEFILE_PROVIDER_MODE)) {
        $env:CASEFILE_PROVIDER_MODE = "live"
    }
    $worker = Start-Process -FilePath $venvPython -ArgumentList @("-m", "casefile.worker") `
        -WorkingDirectory $backendRoot -WindowStyle Hidden `
        -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr -PassThru

    try {
        Start-Sleep -Milliseconds 500
        $worker.Refresh()
        if ($worker.HasExited) {
            Show-LogTail $workerErr
            throw "CaseFile Worker exited during startup."
        }
        $null = Wait-HttpReady "http://127.0.0.1:$ApiPort/health/ready"
        $webResponse = Wait-HttpReady "http://127.0.0.1:$WebPort/"
        if ($webResponse.Content.Length -lt 100) {
            throw "Frontend returned an unexpectedly small HTML document."
        }
    } catch {
        Show-LogTail $apiErr
        Show-LogTail $webErr
        Show-LogTail $workerErr
        throw
    }

    Write-Host "CaseFile is ready."
    Write-Host "  Web: http://127.0.0.1:$WebPort"
    Write-Host "  API: http://127.0.0.1:$ApiPort"
    Write-Host "  API PID: $($api.Id)"
    Write-Host "  Web PID: $($web.Id)"
    Write-Host "  Worker PID: $($worker.Id)"
    Write-Host "  Logs: $runDir"
} finally {
    Pop-Location
}
