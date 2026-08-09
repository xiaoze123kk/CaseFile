param(
    [ValidateRange(1024, 65535)][int]$ApiPort = 18000,
    [ValidateRange(1024, 65535)][int]$WebPort = 13000,
    [switch]$InstallBrowser
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$envFile = Join-Path $repoRoot ".env"
$envExample = Join-Path $repoRoot ".env.example"
$composeFile = Join-Path $repoRoot "infra\compose\docker-compose.yml"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}
$pnpm = (Get-Command pnpm -ErrorAction Stop).Source
$settingsFile = if (Test-Path -LiteralPath $envFile -PathType Leaf) {
    $envFile
} else {
    $envExample
}
$startedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$logFiles = [System.Collections.Generic.List[string]]::new()

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)

    foreach ($line in Get-Content -LiteralPath $Path -Encoding utf8) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Count -eq 2 -and $parts[0] -eq $Name) {
            return $parts[1]
        }
    }
    return $null
}

function Assert-PortAvailable {
    param([int]$Port)

    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $listener) {
        throw "Port $Port is already in use. Stop the existing local service before running the isolated E2E harness."
    }
}

function Wait-HttpReady {
    param([string]$Url, [int]$TimeoutSeconds = 120)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Url."
}

function Stop-ProcessTree {
    param([int]$ProcessId)

    $children = @(
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
    )
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Show-FailureLogs {
    foreach ($path in $logFiles) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Write-Host "--- $(Split-Path -Leaf $path) ---"
            Get-Content -LiteralPath $path -Tail 80 -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Test-Path -LiteralPath $settingsFile -PathType Leaf)) {
    throw "Missing .env.example."
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Backend Python is unavailable. Install backend[dev] before running E2E."
}

$testDatabaseUrl = if (-not [string]::IsNullOrWhiteSpace($env:CASEFILE_TEST_DATABASE_URL)) {
    $env:CASEFILE_TEST_DATABASE_URL
} else {
    Get-DotEnvValue -Path $settingsFile -Name "CASEFILE_TEST_DATABASE_URL"
}
if ([string]::IsNullOrWhiteSpace($testDatabaseUrl)) {
    throw "CASEFILE_TEST_DATABASE_URL is required."
}

if ($WebPort -eq $ApiPort) {
    throw "WebPort and ApiPort must be different."
}

Assert-PortAvailable -Port $WebPort
Assert-PortAvailable -Port $ApiPort

$env:DATABASE_URL = $testDatabaseUrl
$databaseName = (& $python -c (
    "import os; from sqlalchemy.engine import make_url; " +
    "print(make_url(os.environ['DATABASE_URL']).database or '')"
)).Trim()
if ($LASTEXITCODE -ne 0 -or -not $databaseName.EndsWith("_test", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing destructive setup: CASEFILE_TEST_DATABASE_URL must name a disposable *_test database."
}

$null = Get-Command docker -ErrorAction Stop
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is unavailable. Start Docker Desktop and retry."
}

Push-Location $repoRoot
try {
    & docker compose --env-file $settingsFile -f $composeFile up -d postgres-test
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to start the isolated PostgreSQL service."
    }

    $databaseDeadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
    do {
        $health = & docker inspect --format '{{.State.Health.Status}}' casefile-postgres-test 2>$null
        if ($health -eq "healthy") {
            break
        }
        if ([DateTimeOffset]::UtcNow -ge $databaseDeadline) {
            throw "Timed out waiting for the isolated PostgreSQL service."
        }
        Start-Sleep -Milliseconds 500
    } while ($true)

    @'
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

url = os.environ["DATABASE_URL"]
name = make_url(url).database or ""
if not name.lower().endswith("_test"):
    raise SystemExit("Refusing to reset a database that is not named *_test")
engine = create_engine(url)
with engine.begin() as connection:
    connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
    connection.execute(text("CREATE SCHEMA public"))
engine.dispose()
'@ | & $python -
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to reset the isolated test database."
    }

    & $python -m alembic -c backend/alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic failed to migrate the isolated test database."
    }

    @'
import os

from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as connection:
    connection.execute(text("""
        INSERT INTO users (id, display_name, status)
        VALUES (1, 'A Path E2E', 'active')
    """))
engine.dispose()
'@ | & $python -
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to seed the isolated local test user."
    }

    $masterKey = (& $python -c (
        "from casefile.agent_runtime.credentials import generate_master_key; " +
        "print(generate_master_key())"
    )).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($masterKey)) {
        throw "Failed to create an ephemeral E2E encryption key."
    }

    $env:CASEFILE_MASTER_KEY = $masterKey
    $env:CASEFILE_PROVIDER_MODE = "fake"
    $env:CASEFILE_CORS_ORIGINS = "http://127.0.0.1:$WebPort"
    $env:NEXT_PUBLIC_CASEFILE_API_URL = "http://127.0.0.1:$ApiPort/api/v1"
    $env:CASEFILE_E2E_API_URL = $env:NEXT_PUBLIC_CASEFILE_API_URL
    $env:CASEFILE_E2E_BASE_URL = "http://127.0.0.1:$WebPort"
    $env:NEXT_TELEMETRY_DISABLED = "1"

    if ($InstallBrowser) {
        & $pnpm --filter "@casefile/web" exec playwright install chromium
        if ($LASTEXITCODE -ne 0) {
            throw "Playwright Chromium installation failed."
        }
    }

    $runDirectory = Join-Path $repoRoot ("var\e2e\a-path-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Force -Path $runDirectory | Out-Null

    $apiOut = Join-Path $runDirectory "api.out.log"
    $apiErr = Join-Path $runDirectory "api.err.log"
    $workerOut = Join-Path $runDirectory "worker.out.log"
    $workerErr = Join-Path $runDirectory "worker.err.log"
    $webOut = Join-Path $runDirectory "web.out.log"
    $webErr = Join-Path $runDirectory "web.err.log"
    foreach ($path in @($apiOut, $apiErr, $workerOut, $workerErr, $webOut, $webErr)) {
        $logFiles.Add($path)
    }

    $api = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "casefile.api.app:app", "--host", "127.0.0.1", "--port", "$ApiPort") `
        -WorkingDirectory $backendRoot -WindowStyle Hidden `
        -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr -PassThru
    $startedProcesses.Add($api)

    $worker = Start-Process -FilePath $python `
        -ArgumentList @("-m", "casefile.worker") `
        -WorkingDirectory $backendRoot -WindowStyle Hidden `
        -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr -PassThru
    $startedProcesses.Add($worker)

    $web = Start-Process -FilePath $pnpm `
        -ArgumentList @("--filter", "@casefile/web", "exec", "next", "dev", "--hostname", "127.0.0.1", "--port", "$WebPort") `
        -WorkingDirectory $repoRoot -WindowStyle Hidden `
        -RedirectStandardOutput $webOut -RedirectStandardError $webErr -PassThru
    $startedProcesses.Add($web)

    Wait-HttpReady -Url "http://127.0.0.1:$ApiPort/health/ready"
    Wait-HttpReady -Url "http://127.0.0.1:$WebPort/"

    $worker.Refresh()
    if ($worker.HasExited) {
        throw "CaseFile Worker exited during E2E startup."
    }

    & $pnpm --filter "@casefile/web" run e2e:a-path
    if ($LASTEXITCODE -ne 0) {
        throw "A-path browser E2E failed. Logs: $runDirectory"
    }

    Write-Host "A-path browser E2E passed against Next.js, FastAPI, Worker, and PostgreSQL."
    Write-Host "Logs: $runDirectory"
} catch {
    Show-FailureLogs
    throw
} finally {
    for ($index = $startedProcesses.Count - 1; $index -ge 0; $index -= 1) {
        $process = $startedProcesses[$index]
        $process.Refresh()
        if (-not $process.HasExited) {
            Stop-ProcessTree -ProcessId $process.Id
        }
    }
    Pop-Location
}
