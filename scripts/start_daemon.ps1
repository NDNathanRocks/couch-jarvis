<#
.SYNOPSIS
    Runs the CouchJarvis PC daemon forever: restarts it if it crashes, with backoff.

.DESCRIPTION
    - Single instance (a second copy exits immediately).
    - Logs to %LOCALAPPDATA%\CouchJarvis\logs (7 days kept). Pairing URLs/secrets are never logged.
    - Crash-loop protection: the restart delay doubles (2 s .. 60 s) while the daemon keeps dying
      quickly, and resets once it has stayed up for a minute.
    The daemon itself supervises Edge (watchdog), so this only has to keep the daemon alive.

.PARAMETER DryRun
    Pass --dry-run (fake Edge/Windows/LLMs). Useful to check the install.

.PARAMETER Hud
    Pass --hud (TV overlay with the pairing QR). Needs an interactive desktop session.

.PARAMETER MaxRestarts
    Stop after this many restarts (0 = never stop).

.EXAMPLE
    .\start_daemon.ps1 -Hud
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Interactive console tool: output is for a human, not the pipeline.')]
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Hud,
    [ValidateRange(0, 100000)]
    [int]$MaxRestarts = 0,
    [string]$LogDir = (Join-Path $env:LOCALAPPDATA 'CouchJarvis\logs')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$entry = Join-Path $repo 'pc-daemon\main.py'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python venv not found at $python. Create it first: uv venv --python 3.12 .venv ; uv pip install -r requirements.txt"
}
if (-not (Test-Path -LiteralPath $entry)) { throw "Daemon entrypoint missing: $entry" }

$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\CouchJarvisDaemon', [ref]$createdNew)
if (-not $createdNew) {
    Write-Warning 'CouchJarvis is already running in this session.'
    exit 1
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Get-ChildItem -Path $LogDir -Filter 'daemon-*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Start-Process joins array items with spaces without quoting them, and repo paths may contain spaces.
$daemonArgs = @("`"$entry`"")
if ($DryRun) { $daemonArgs += '--dry-run' }
if ($Hud) { $daemonArgs += '--hud' }

function Write-Log {
    param([string]$Message)
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    Add-Content -LiteralPath (Join-Path $LogDir ('daemon-{0:yyyyMMdd}.log' -f (Get-Date))) -Value $line
    Write-Host $line
}

try {
    $delay = 2
    $restarts = 0
    while ($true) {
        $day = '{0:yyyyMMdd}' -f (Get-Date)
        $out = Join-Path $LogDir "daemon-$day.out.log"
        $err = Join-Path $LogDir "daemon-$day.err.log"
        Write-Log "starting daemon (restart #$restarts): $python $($daemonArgs -join ' ')"
        $started = Get-Date
        $process = Start-Process -FilePath $python -ArgumentList $daemonArgs -WorkingDirectory $repo `
            -NoNewWindow -PassThru -Wait -RedirectStandardOutput $out -RedirectStandardError $err
        $ranSeconds = ((Get-Date) - $started).TotalSeconds
        Write-Log ("daemon exited with code {0} after {1:N0}s" -f $process.ExitCode, $ranSeconds)

        $restarts++
        if ($MaxRestarts -gt 0 -and $restarts -ge $MaxRestarts) {
            Write-Log "reached MaxRestarts=$MaxRestarts, stopping"
            break
        }
        if ($ranSeconds -ge 60) { $delay = 2 }  # it was healthy for a while: start the ladder over
        Write-Log "restarting in ${delay}s"
        Start-Sleep -Seconds $delay
        $delay = [Math]::Min($delay * 2, 60)    # a quick crash next time waits twice as long
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
