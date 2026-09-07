[CmdletBinding()]
param(
    [switch]$SkipDependencySync,
    [ValidateRange(1, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1, 65535)][int]$WebPort = 3000
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

function Show-LogTail([string]$path) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        Write-Host "--- $path ---"
        Get-Content -LiteralPath $path -Tail 20
    }
}

. (Join-Path $PSScriptRoot "startup-runtime.ps1")
if ($ApiPort -eq $WebPort) { throw 'API and Web ports must differ.' }
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
try {
    $startupLock = [IO.File]::Open((Join-Path $runDir 'startup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
} catch { throw 'Another CaseFile startup is already running.' }

Push-Location $repoRoot
$transcriptStarted = $false
try {
    $null = Start-Transcript -Path (Join-Path $runDir ("startup-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff") + ".log"))
    $transcriptStarted = $true
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

    $api = Get-WorkspacePortOwner $ApiPort $repoRoot
    $web = Get-WorkspacePortOwner $WebPort $repoRoot
    $workers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like '*-m casefile.worker*' -and
            $_.CommandLine -like "*$venvPython*" })
    $worker = if ($workers.Count) { Get-Process -Id $workers[0].ProcessId } else { $null }
    Start-CaseFileDocker

    if (-not $SkipDependencySync -and -not ($api -or $web -or $worker)) {
        $uv = Get-Command uv -ErrorAction SilentlyContinue
        if (-not $uv) { throw 'uv was not found. Install uv, or use -SkipDependencySync when dependencies are already installed.' }
        Write-Host "Syncing workspace dependencies..."
        $sync = Invoke-StartupCommand $env:ComSpec "/d /s /c `"`"$($pnpm.Source)`" install --frozen-lockfile`"" 600
        Write-Host $sync.Output
        if ($sync.ExitCode -ne 0) {
            Write-Host $sync.Error
            throw "pnpm dependency sync failed."
        }
        $sync = Invoke-StartupCommand $uv.Source 'sync --project backend --extra dev' 600
        if ($sync.ExitCode -ne 0) { Write-Host $sync.Error; throw "Backend dependency sync failed." }
    } elseif (-not $SkipDependencySync) {
        Write-Host "Services are running; dependency sync skipped to preserve active tasks."
    }

    Write-Host "Preparing PostgreSQL and applying migrations..."
    $bootstrap = Invoke-StartupCommand powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'bootstrap.ps1')`"" 240
    Write-Host $bootstrap.Output
    if ($bootstrap.ExitCode -ne 0) {
        Write-Host $bootstrap.Error
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

    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $apiOut = Join-Path $runDir "api-$stamp.out.log"
    $apiErr = Join-Path $runDir "api-$stamp.err.log"
    $webOut = Join-Path $runDir "web-$stamp.out.log"
    $webErr = Join-Path $runDir "web-$stamp.err.log"
    $workerOut = Join-Path $runDir "worker-$stamp.out.log"
    $workerErr = Join-Path $runDir "worker-$stamp.err.log"

    if (-not $api) {
        $api = Start-Process -FilePath $venvPython `
            -ArgumentList @("-m", "uvicorn", "casefile.api.app:app", "--host", "127.0.0.1", "--port", "$ApiPort") `
            -WorkingDirectory $backendRoot -WindowStyle Hidden `
            -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr -PassThru
    } else { Write-Host "Reusing API PID $($api.Id)." }
    # Start pnpm through cmd.exe so .cmd shims work reliably.
    # Pass --port explicitly so an external PORT value cannot override -WebPort.
    if (-not $web) {
        $web = Start-Process -FilePath $env:ComSpec `
            -ArgumentList @("/c", "`"$($pnpm.Source)`" --filter @casefile/web dev --port $WebPort") `
            -WorkingDirectory $repoRoot -WindowStyle Hidden `
            -RedirectStandardOutput $webOut -RedirectStandardError $webErr -PassThru
    } else { Write-Host "Reusing Web PID $($web.Id)." }
    if ([string]::IsNullOrWhiteSpace($env:CASEFILE_PROVIDER_MODE)) {
        $env:CASEFILE_PROVIDER_MODE = "live"
    }
    if (-not $worker) {
        $worker = Start-Process -FilePath $venvPython -ArgumentList @("-m", "casefile.worker") `
            -WorkingDirectory $backendRoot -WindowStyle Hidden `
            -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr -PassThru

    } else { Write-Host "Reusing Worker PID $($worker.Id); active tasks are preserved." }

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
        $worker.Refresh()
        if ($worker.HasExited) { throw 'CaseFile Worker exited before startup completed.' }
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
} catch {
    Write-Host "Startup failed: $($_.Exception.Message)"
    throw
} finally {
    if ($transcriptStarted) { $null = Stop-Transcript }
    Pop-Location
    $startupLock.Dispose()
}
