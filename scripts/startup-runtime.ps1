# Windows PowerShell 5.1-compatible startup operations. Dot-sourcing has no side effects.
function Invoke-StartupCommand {
    param([string]$FilePath, [string]$Arguments, [int]$TimeoutSeconds = 15)
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = $Arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            # Only terminate the command tree launched by this invocation.
            & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F *> $null
            throw "Command timed out after ${TimeoutSeconds}s: $FilePath"
        }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $stdout.Result; Error = $stderr.Result }
    } finally { $process.Dispose() }
}

function Test-DockerEngine {
    try { return (Invoke-StartupCommand docker.exe 'info --format {{.ServerVersion}}' 8).ExitCode -eq 0 }
    catch { return $false }
}

function Backup-DockerRuntime {
    param([string]$LocalRoot)
    $root = [IO.Path]::GetFullPath($LocalRoot).TrimEnd('\')
    $paths = @((Join-Path $root 'Docker\run'), (Join-Path $root 'docker-secrets-engine'))
    # Validate every target before moving either directory. Never traverse reparse directories.
    foreach ($path in $paths) {
        if (-not [IO.Path]::GetFullPath($path).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Docker runtime path escaped LOCALAPPDATA.'
        }
        $cursor = $path
        while ($cursor -and $cursor.Length -ge $root.Length) {
            if (Test-Path -LiteralPath $cursor) {
                $item = Get-Item -LiteralPath $cursor -Force
                if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "Unsafe Docker runtime directory: $cursor"
                }
            }
            $cursor = Split-Path -Parent $cursor
        }
    }
    $secrets = $paths[1]
    if (Test-Path -LiteralPath $secrets) {
        $unexpected = @(Get-ChildItem -LiteralPath $secrets -Force | Where-Object { $_.Name -ne 'engine.sock' -or $_.PSIsContainer })
        if ($unexpected.Count) { throw 'Secrets runtime contains unexpected data; automatic recovery stopped.' }
    }
    $suffix = 'recovery-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path) {
            $backup = "$path-$suffix"
            Move-Item -LiteralPath $path -Destination $backup
            Write-Host "Docker runtime backup: $backup"
        }
    }
}

function Test-DockerSocketFailure {
    param([string[]]$Lines, [DateTimeOffset]$Since)
    foreach ($line in $Lines) {
        if ($line -match '^\[(?<time>[^\]]+)\].*(dockerInference|engine\.sock).*(cannot be accessed by the system|filename, directory name, or volume label syntax is incorrect)') {
            $time = [DateTimeOffset]::MinValue
            if ([DateTimeOffset]::TryParse($Matches.time, [ref]$time) -and $time -ge $Since) { return $true }
        }
    }
    return $false
}

function Start-CaseFileDocker {
    if (Test-DockerEngine) { Write-Host 'Docker Engine is ready.'; return }
    $install = Join-Path $env:ProgramFiles 'Docker\Docker'
    $desktop = Join-Path $install 'Docker Desktop.exe'
    if (-not (Test-Path -LiteralPath $desktop)) { throw 'Docker Desktop is not installed.' }
    $log = Join-Path $env:LOCALAPPDATA 'Docker\log\host\com.docker.backend.exe.log'
    # Include the current failed Desktop session, but ignore older sessions' errors.
    $since = [DateTimeOffset]::UtcNow
    $existing = @(Get-Process -Name 'com.docker.backend', 'Docker Desktop' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($install + '\', [StringComparison]::OrdinalIgnoreCase) })
    if ($existing.Count) { $since = [DateTimeOffset]($existing | Sort-Object StartTime | Select-Object -First 1).StartTime }
    Write-Host 'Starting Docker Desktop (bounded readiness checks)...'
    Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
        $knownFailure = $false
        do {
            if (Test-DockerEngine) { return }
            if (Test-Path -LiteralPath $log) {
                $knownFailure = Test-DockerSocketFailure @(Get-Content -LiteralPath $log -Tail 2000) $since
                if ($knownFailure) { break }
            }
            Start-Sleep -Seconds 2
        } while ([DateTimeOffset]::UtcNow -lt $deadline)
        if (-not $knownFailure -or $attempt -eq 1) {
            throw "Docker did not become ready. See $log. No database or image data was reset."
        }
        Write-Host 'Detected failed Docker runtime sockets; stopping Desktop before reversible recovery...'
        $dockerProcesses = @(Get-CimInstance Win32_Process | Where-Object {
            ($_.Name -in @('Docker Desktop.exe', 'com.docker.backend.exe') -or
                ($_.Name -eq 'docker.exe' -and $_.CommandLine -match '\bdesktop\s+(start|restart)\b')) -and
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($install + '\', [StringComparison]::OrdinalIgnoreCase)
        })
        foreach ($entry in $dockerProcesses) {
            Stop-Process -Id $entry.ProcessId -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $entry.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
        }
        $remaining = @(Get-Process -Name 'Docker Desktop', 'com.docker.backend' -ErrorAction SilentlyContinue)
        if ($remaining.Count) { throw 'Docker is still running; runtime recovery was not performed.' }
        Backup-DockerRuntime $env:LOCALAPPDATA
        $since = [DateTimeOffset]::UtcNow
        Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
    }
}

function Test-WorkspaceProcess {
    param([int]$ProcessId, [string]$Workspace)
    $visited = @{}
    while ($ProcessId -gt 0 -and -not $visited.ContainsKey($ProcessId)) {
        $visited[$ProcessId] = $true
        $entry = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
        if (-not $entry) { return $false }
        if ($entry.CommandLine -and $entry.CommandLine.IndexOf($Workspace.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
        $ProcessId = $entry.ParentProcessId
    }
    return $false
}

function Get-WorkspacePortOwner {
    param([int]$Port, [string]$Workspace)
    $owners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -eq $Port } | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($owner in $owners) {
        if (-not (Test-WorkspaceProcess $owner $Workspace)) { throw "Port $Port belongs to another process ($owner). Choose another port; it was not stopped." }
    }
    if ($owners.Count) { return Get-Process -Id $owners[0] }
    return $null
}
