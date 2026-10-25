<#
.SYNOPSIS
    Upgrade test required before every release (plan.md section 12, rule 4).

.DESCRIPTION
    Installs an OLD published installer silently into a throwaway folder, starts the app, installs the NEW
    installer over it, and checks: the version changed, the same folder was reused, the running app was closed
    and started again, the user's data (room.json, config.env, the settings folder) was not touched, and the
    uninstaller keeps user data by default. Run it on a PC (or Windows Sandbox / VM) with no Couch Jarvis
    installed: the installers share one registry entry, so it refuses to run when one is registered.

.PARAMETER OldInstaller
    The previously published CouchJarvis-Setup.exe (download it from the GitHub release).

.PARAMETER NewInstaller
    The candidate installer\dist\CouchJarvis-Setup.exe.

.PARAMETER Work
    A SHORT scratch folder (Windows rejects very long install paths), e.g. $env:LOCALAPPDATA\cjt.

.EXAMPLE
    .\scripts\test_installer_upgrade.ps1 -OldInstaller C:\temp\Setup-0.2.0.exe -NewInstaller .\installer\dist\CouchJarvis-Setup.exe -Work $env:LOCALAPPDATA\cjt
#>
param(
    [Parameter(Mandatory)][string]$OldInstaller,
    [Parameter(Mandatory)][string]$NewInstaller,
    [Parameter(Mandatory)][string]$Work
)
$ErrorActionPreference = 'Stop'
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{7B1E3C52-5A9D-4B5E-9C1A-2F4D8E6A0C31}_is1'
$data = Join-Path $env:APPDATA 'CouchJarvis'
$room = Join-Path $data 'room.json'
$config = Join-Path $data 'config.env'
$app = Join-Path $Work 'app'
$results = [ordered]@{}
function Check($name, $ok, $detail = '') { $results[$name] = if ($ok) { "PASS $detail" } else { "FAIL $detail" } }
function Hash($p) { if (Test-Path -LiteralPath $p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash } else { 'absent' } }
# Run a program and wait for that process only (not for anything it starts), with a time limit.
function Run($file, $arguments, $limitSeconds = 180) {
    $p = Start-Process -FilePath $file -ArgumentList $arguments -PassThru
    if (-not $p.WaitForExit($limitSeconds * 1000)) { $p.Kill(); return -999 }
    return $p.ExitCode
}

if (Test-Path -LiteralPath $key) { throw 'An install is already registered on this PC: refusing to touch it.' }
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$roomBefore = Hash $room
$configBefore = Hash $config
$marker = Join-Path $data 'upgrade-test.marker'
New-Item -ItemType Directory -Force -Path $data | Out-Null
Set-Content -LiteralPath $marker -Value 'must survive the update'
try {
    # 1. install the published v0.2.0 into a throwaway folder
    $code = Run $OldInstaller "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=`"$app`" /LOG=`"$Work\old.log`""
    Check 'old installer exit code 0' ($code -eq 0) "($code)"
    $old = (Get-ItemProperty -LiteralPath $key).DisplayVersion
    Check 'old version registered' ($old -eq '0.2.0') $old
    $oldExeTime = (Get-Item -LiteralPath (Join-Path $app 'CouchJarvis.exe')).LastWriteTimeUtc
    $oldDefaults = Get-Content -LiteralPath (Join-Path $app '_internal\installer\defaults.env') -Raw
    Check 'old build has the old defaults' ($oldDefaults -match 'large-v3-turbo|CJ_STT_DEVICE=cuda')

    # a running copy (a windowless background process, like the real app) must be stopped by the update
    $running = Start-Process -FilePath (Join-Path $app 'CouchJarvis.exe') -ArgumentList '--dry-run' -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 5
    Check 'the old app is running before the update' (-not $running.HasExited)

    # 2. install the new build over it, without naming a folder (it must reuse the old one)
    $code = Run $NewInstaller "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$Work\new.log`""
    Check 'new installer exit code 0' ($code -eq 0) "($code)"
    $new = (Get-ItemProperty -LiteralPath $key).DisplayVersion
    Check 'new version registered' ($new -match '^\d+\.\d+\.\d+$' -and $new -ne '0.2.0') $new
    $loc = (Get-ItemProperty -LiteralPath $key).InstallLocation.TrimEnd('\')
    Check 'updated in place (same folder)' ($loc -eq $app) $loc
    Check 'files were replaced' ((Get-Item -LiteralPath (Join-Path $app 'CouchJarvis.exe')).LastWriteTimeUtc -gt $oldExeTime)
    $newDefaults = Get-Content -LiteralPath (Join-Path $app '_internal\installer\defaults.env') -Raw
    Check 'new defaults are in place' ($newDefaults -match 'CJ_STT_DEVICE=auto')
    Check 'the old running copy was closed' ($running.HasExited)
    Start-Sleep -Seconds 8
    $relaunched = @(Get-Process -Name CouchJarvis -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $running.Id })
    Check 'a silent update starts the app again' ($relaunched.Count -ge 1) "($($relaunched.Count) process)"
    Get-Process -Name CouchJarvis -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3

    # 3. the user's data is untouched
    Check 'room.json untouched' ((Hash $room) -eq $roomBefore)
    # (a config.env that did not exist is created by the app's first run: only an existing one must be unchanged)
    Check 'config.env untouched' (($configBefore -eq 'absent') -or ((Hash $config) -eq $configBefore))
    Check 'settings folder not wiped' (Test-Path -LiteralPath $marker)
    $links = @(Get-ChildItem -Recurse -Path (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs') -Filter 'Couch Jarvis.lnk' -ErrorAction SilentlyContinue)
    Check 'one Start-menu shortcut' ($links.Count -eq 1) "($($links.Count))"

    # 4. uninstall (the prompt to delete settings defaults to No) and confirm the data is still there
    $unins = Get-ChildItem -LiteralPath $app -Filter 'unins*.exe' | Select-Object -First 1
    $code = Run $unins.FullName "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$Work\unins.log`"" 120
    Start-Sleep -Seconds 3
    if ($code -ne 0 -and (Test-Path -LiteralPath "$Work\unins.log")) {
        Write-Host '--- last lines of the uninstall log ---'
        Get-Content -LiteralPath "$Work\unins.log" -Tail 12 | ForEach-Object { Write-Host $_ }
    }
    Check 'uninstall exit code 0' ($code -eq 0) "($code)"
    Check 'uninstall removed the registration' (-not (Test-Path -LiteralPath $key))
    Check 'uninstall kept room.json' ((Hash $room) -eq $roomBefore)
    Check 'uninstall kept config.env' (($configBefore -eq 'absent') -or ((Hash $config) -eq $configBefore))
}
catch { $results['SCRIPT ERROR'] = "FAIL $($_.Exception.Message)" }
finally {
    Get-Process -Name CouchJarvis, _unins -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
    if ($configBefore -eq 'absent') { Remove-Item -LiteralPath $config -Force -ErrorAction SilentlyContinue }
}
$results.GetEnumerator() | ForEach-Object { '{0,-46} {1}' -f $_.Key, $_.Value }
"FAILED CHECKS: $(@($results.Values | Where-Object { $_ -like 'FAIL*' }).Count)"
