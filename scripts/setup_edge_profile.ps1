<#
.SYNOPSIS
    Prepares the dedicated Microsoft Edge "TV profile" that CouchJarvis drives over CDP.

.DESCRIPTION
    Chromium-based browsers refuse remote debugging on the *default* profile, so the daemon uses
    its own --user-data-dir. This script creates that directory, adds a "Edge (TV)" desktop
    shortcut that starts Edge with --remote-debugging-port, and (optionally) launches Edge and
    verifies that the debug port answers. Log in to YouTube / YouTube Music once in this profile
    and the session persists. The daemon's watchdog relaunches Edge with the same flags if it dies.

.PARAMETER ProfileDir
    User-data directory. Default: %LOCALAPPDATA%\CouchJarvis\edge-profile (matches CJ_EDGE_PROFILE_DIR).

.PARAMETER Port
    Remote debugging port. Default 9222 (matches CJ_CDP_PORT).

.PARAMETER EdgePath
    Path to msedge.exe. Auto-detected when omitted.

.PARAMETER Launch
    Start Edge now and verify the CDP endpoint responds.

.PARAMETER NoShortcut
    Do not create the desktop shortcut.

.EXAMPLE
    .\setup_edge_profile.ps1 -Launch

.EXAMPLE
    .\setup_edge_profile.ps1 -WhatIf
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Interactive console tool: output is for a human, not the pipeline.')]
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ProfileDir = (Join-Path $env:LOCALAPPDATA 'CouchJarvis\edge-profile'),
    [ValidateRange(1024, 65535)]
    [int]$Port = 9222,
    [string]$EdgePath,
    [switch]$Launch,
    [switch]$NoShortcut
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Find-Edge {
    param([string]$Explicit)
    if ($Explicit) {
        if (Test-Path -LiteralPath $Explicit) { return (Resolve-Path -LiteralPath $Explicit).Path }
        throw "Edge not found at '$Explicit'."
    }
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) { return $path }
    }
    throw 'Microsoft Edge was not found. Install it or pass -EdgePath.'
}

function Test-CdpEndpoint {
    param([int]$Port, [int]$TimeoutSeconds = 15)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $info = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2
            return $info
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $null
}

$edge = Find-Edge -Explicit $EdgePath
$edgeArgs = "--remote-debugging-port=$Port --user-data-dir=`"$ProfileDir`" --no-first-run --no-default-browser-check"
Write-Host "Edge:        $edge"
Write-Host "Profile dir: $ProfileDir"
Write-Host "CDP port:    $Port"

if (Test-Path -LiteralPath $ProfileDir) {
    Write-Host 'Profile directory already exists - keeping it (your logins are preserved).'
} elseif ($PSCmdlet.ShouldProcess($ProfileDir, 'Create Edge TV profile directory')) {
    New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
    Write-Host 'Created profile directory.'
}

if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $lnk = Join-Path $desktop 'Edge (TV).lnk'
    if ($PSCmdlet.ShouldProcess($lnk, 'Create/refresh desktop shortcut')) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($lnk)
        $shortcut.TargetPath = $edge
        $shortcut.Arguments = $edgeArgs
        $shortcut.WorkingDirectory = Split-Path -Parent $edge
        $shortcut.Description = 'Edge with the CouchJarvis TV profile and debug port'
        $shortcut.Save()
        Write-Host "Shortcut:    $lnk"
    }
}

if ($Launch) {
    if ($PSCmdlet.ShouldProcess($edge, "Launch Edge on debug port $Port")) {
        $existing = Test-CdpEndpoint -Port $Port -TimeoutSeconds 1
        if ($existing) {
            Write-Host "A browser already answers on port $Port ($($existing.Browser))."
        } else {
            Start-Process -FilePath $edge -ArgumentList $edgeArgs
            $info = Test-CdpEndpoint -Port $Port
            if ($info) {
                Write-Host "CDP is up: $($info.Browser)" -ForegroundColor Green
            } else {
                Write-Warning "Edge started but port $Port did not answer within 15 s. Close all Edge windows that use this profile and retry."
            }
        }
    }
}

Write-Host ''
Write-Host 'Add these to your .env if you changed the defaults:'
Write-Host "  CJ_EDGE_PATH=$edge"
Write-Host "  CJ_EDGE_PROFILE_DIR=$ProfileDir"
Write-Host "  CJ_CDP_PORT=$Port"
Write-Host ''
Write-Host 'Next: open "Edge (TV)", sign in to YouTube / YouTube Music once, then start the daemon.'
