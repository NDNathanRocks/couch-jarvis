<#
.SYNOPSIS
    Registers (or removes) a Task Scheduler job that starts CouchJarvis when you log in.

.DESCRIPTION
    The daemon drives a real desktop (Edge, mouse, TV overlay), so it must run in your
    *interactive* session - not as a service. The task runs start_daemon.ps1 at logon for the
    current user with limited rights and no time limit; start_daemon.ps1 restarts the daemon if
    it crashes.

.PARAMETER Uninstall
    Remove the task.

.PARAMETER NoHud
    Do not pass -Hud (skip the TV overlay).

.EXAMPLE
    .\install_startup_task.ps1 -WhatIf

.EXAMPLE
    .\install_startup_task.ps1
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Interactive console tool: output is for a human, not the pipeline.')]
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Uninstall,
    [switch]$NoHud,
    [string]$TaskName = 'CouchJarvis'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled task')) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Removed task '$TaskName'."
        }
    } else {
        Write-Host "Task '$TaskName' is not installed."
    }
    return
}

$startScript = Join-Path $PSScriptRoot 'start_daemon.ps1'
if (-not (Test-Path -LiteralPath $startScript)) { throw "Missing $startScript" }

$argument = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$startScript`""
if (-not $NoHud) { $argument += ' -Hud' }

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

if ($PSCmdlet.ShouldProcess($TaskName, "Register logon task for $user")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'CouchJarvis PC daemon (voice/vision TV remote)' -Force | Out-Null
    Write-Host "Installed task '$TaskName' (runs at logon for $user)."
    Write-Host "Start it now with:  Start-ScheduledTask -TaskName $TaskName"
}
