$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'startup-runtime.ps1')

function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

# Fixtures are retained in ignored var/dev for inspection; never touch real Docker data.
$fixture = Join-Path (Split-Path -Parent $PSScriptRoot) ('var\dev\startup-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture -Force | Out-Null
$result = Invoke-StartupCommand powershell.exe '-NoProfile -Command "Write-Output ready; exit 7"' 10
Assert ($result.ExitCode -eq 7 -and $result.Output.Trim() -eq 'ready') 'Exit code or stdout was lost.'
$clock = [Diagnostics.Stopwatch]::StartNew()
$timedOut = $false
try { $null = Invoke-StartupCommand powershell.exe '-NoProfile -Command "Start-Sleep -Seconds 60"' 1 }
catch { $timedOut = $_.Exception.Message -like '*timed out*' }
Assert ($timedOut -and $clock.Elapsed.TotalSeconds -lt 10) 'A hung command did not stop within its deadline.'

$since = [DateTimeOffset]'2026-09-07T05:00:00Z'
$inference = '[2026-09-07T05:52:23.978292600Z][com.docker.backend.exe] starting services: initializing Inference manager: dockerInference: The file cannot be accessed by the system.'
$secrets = '[2026-09-07T05:52:23.978292600Z][com.docker.backend.exe] initializing Secrets Engine: engine.sock: The filename, directory name, or volume label syntax is incorrect.'
Assert (Test-DockerSocketFailure @($inference) $since) 'Inference failure was missed.'
Assert (Test-DockerSocketFailure @($secrets) $since) 'Secrets failure was missed.'
Assert (-not (Test-DockerSocketFailure @($inference) $since.AddHours(1))) 'An old session triggered recovery.'
Assert (-not (Test-DockerSocketFailure @('[2026-09-07T05:52:23Z] connection refused') $since)) 'An unrelated failure triggered recovery.'

$local = Join-Path $fixture 'local'
New-Item -ItemType Directory -Path "$local\Docker\run", "$local\docker-secrets-engine", "$local\Docker\wsl" -Force | Out-Null
Set-Content -LiteralPath "$local\Docker\run\dockerInference" -Value 'socket-fixture'
Set-Content -LiteralPath "$local\docker-secrets-engine\engine.sock" -Value 'secrets-fixture'
Set-Content -LiteralPath "$local\Docker\wsl\data.vhdx" -Value 'preserve-volume'
Backup-DockerRuntime $local
Assert (-not (Test-Path -LiteralPath "$local\Docker\run")) 'Inference runtime was not moved.'
Assert (-not (Test-Path -LiteralPath "$local\docker-secrets-engine")) 'Secrets runtime was not moved in the same recovery.'
$runBackup = Get-ChildItem -LiteralPath "$local\Docker" -Filter 'run-recovery-*'
$secretsBackup = Get-ChildItem -LiteralPath $local -Filter 'docker-secrets-engine-recovery-*'
Assert ((Get-Content -LiteralPath (Join-Path $runBackup.FullName 'dockerInference')) -eq 'socket-fixture') 'Inference backup was lost.'
Assert ((Get-Content -LiteralPath (Join-Path $secretsBackup.FullName 'engine.sock')) -eq 'secrets-fixture') 'Secrets backup was lost.'
Assert ((Get-Content -LiteralPath "$local\Docker\wsl\data.vhdx") -eq 'preserve-volume') 'Persistent data was modified.'

New-Item -ItemType Directory -Path "$local\Docker\run", "$local\docker-secrets-engine" -Force | Out-Null
Set-Content -LiteralPath "$local\docker-secrets-engine\unexpected.db" -Value 'important'
$blocked = $false
try { Backup-DockerRuntime $local } catch { $blocked = $_.Exception.Message -like '*unexpected data*' }
Assert $blocked 'Unknown secrets data did not block recovery.'
Assert (Test-Path -LiteralPath "$local\Docker\run") 'Recovery moved one directory before validating the other.'

# Verify the orchestrator never launches/stops Desktop when the engine is already healthy.
& {
    function Test-DockerEngine { return $true }
    function Start-Process { throw 'Healthy Docker must not be restarted.' }
    function Backup-DockerRuntime { throw 'Healthy Docker must not be moved.' }
    Start-CaseFileDocker
}

# Exercise the full failed-start -> backup both endpoints -> healthy retry path.
& {
    $oldProgramFiles = $env:ProgramFiles
    $oldLocal = $env:LOCALAPPDATA
    try {
        $env:ProgramFiles = Join-Path $fixture 'programs'
        $env:LOCALAPPDATA = Join-Path $fixture 'recovery-local'
        New-Item -ItemType Directory -Force -Path "$env:ProgramFiles\Docker\Docker", "$env:LOCALAPPDATA\Docker\log\host", "$env:LOCALAPPDATA\Docker\run", "$env:LOCALAPPDATA\docker-secrets-engine" | Out-Null
        Set-Content -LiteralPath "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe" -Value 'fixture'
        Set-Content -LiteralPath "$env:LOCALAPPDATA\Docker\run\dockerInference" -Value 'socket'
        Set-Content -LiteralPath "$env:LOCALAPPDATA\docker-secrets-engine\engine.sock" -Value 'socket'
        $script:fakeLaunches = 0
        function Test-DockerEngine { return $script:fakeLaunches -eq 2 }
        function Get-Process { return @() }
        function Get-CimInstance { return @() }
        function Stop-Process { throw 'No actual processes may be stopped by this test.' }
        function Start-Process {
            $script:fakeLaunches++
            $timestamp = [DateTimeOffset]::UtcNow.ToString('o')
            Set-Content -LiteralPath "$env:LOCALAPPDATA\Docker\log\host\com.docker.backend.exe.log" -Value "[$timestamp] dockerInference: The file cannot be accessed by the system."
        }
        Start-CaseFileDocker
        Assert ($script:fakeLaunches -eq 2) 'Recovery did not make exactly one retry.'
        Assert (-not (Test-Path -LiteralPath "$env:LOCALAPPDATA\Docker\run")) 'Recovery left the inference endpoint behind.'
        Assert (-not (Test-Path -LiteralPath "$env:LOCALAPPDATA\docker-secrets-engine")) 'Recovery left the secrets endpoint behind.'
        # A second known failure must stop rather than retry indefinitely.
        $script:fakeLaunches = 0
        function Test-DockerEngine { return $false }
        $failed = $false
        try { Start-CaseFileDocker } catch { $failed = $_.Exception.Message -like '*did not become ready*' }
        Assert ($failed -and $script:fakeLaunches -eq 2) 'Recovery retry limit was not enforced.'
    } finally {
        $env:ProgramFiles = $oldProgramFiles
        $env:LOCALAPPDATA = $oldLocal
    }
}

# Port conflicts must fail without terminating the unrelated listener.
$listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
try {
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $blocked = $false
    try { $null = Get-WorkspacePortOwner $port 'Z:\unrelated-workspace' }
    catch { $blocked = $_.Exception.Message -like '*belongs to another process*' }
    Assert $blocked 'Unrelated port ownership was accepted.'
    Assert ($listener.Server.IsBound) 'Unrelated listener was interrupted.'
} finally { $listener.Stop() }
Write-Host "Startup regression checks passed. Fixtures: $fixture"
