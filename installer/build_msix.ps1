<#
.SYNOPSIS
    Packages the built app (installer\dist\CouchJarvis) as an MSIX for the Microsoft Store.

.DESCRIPTION
    Run installer\build.ps1 first. This script copies the PyInstaller output into a package layout, adds the
    tiles, fills installer\msix\AppxManifest.xml and runs makeappx. The Store signs the package itself, so a
    Store upload needs no certificate. To install a package on a test PC, sign it with -CertThumbprint (a
    certificate whose subject equals -Publisher, trusted in Local Machine > Trusted People). Windows refuses
    unsigned packages that contain an .exe, so there is no unsigned option.

.PARAMETER IdentityName
    Package identity name. For the Store, copy it from Partner Center (Product identity > Package/Identity/Name).

.PARAMETER Publisher
    Publisher subject, e.g. CN=1A2B3C4D-.... For the Store, copy it from Partner Center (Package/Identity/Publisher).

.PARAMETER ToolsDir
    Folder holding makeappx.exe and signtool.exe. Default: the newest Windows SDK, if installed.

.PARAMETER CertThumbprint
    Sign with this certificate from Cert:\CurrentUser\My (local install tests only).

.EXAMPLE
    .\installer\build_msix.ps1 -IdentityName <from Partner Center> -Publisher <from Partner Center>
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Interactive console tool: output is for a human, not the pipeline.')]
[CmdletBinding()]
param(
    [string]$IdentityName = 'CouchJarvis.Dev',
    [string]$Publisher = 'CN=CouchJarvisDev',
    [string]$DisplayName = 'Couch Jarvis',
    [string]$PublisherDisplayName = 'Couch Jarvis',
    [string]$Description = 'Control the PC on your TV from any phone: voice, buttons, touchpad and keyboard.',
    [string]$Version,
    [string]$ToolsDir,
    [string]$CertThumbprint,
    [switch]$AnyChannel  # package a non-store build anyway (local packaging tests only)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$app = Join-Path $PSScriptRoot 'dist\CouchJarvis'
if (-not (Test-Path -LiteralPath (Join-Path $app 'CouchJarvis.exe'))) { throw "Build the app first: installer\build.ps1 -Channel store (missing $app)" }
$infoFile = Join-Path $app '_internal\installer\build_info.json'
$builtChannel = if (Test-Path -LiteralPath $infoFile) { (Get-Content -LiteralPath $infoFile -Raw | ConvertFrom-Json).channel } else { 'none' }
if ($builtChannel -ne 'store' -and -not $AnyChannel) {
    throw "installer\dist\CouchJarvis is a '$builtChannel' build. Rebuild with: installer\build.ps1 -Channel store"
}

if (-not $Version) {
    $versionSource = Get-Content -LiteralPath (Join-Path $repo 'pc-daemon\version.py') -Raw
    if ($versionSource -notmatch '__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') { throw 'pc-daemon\version.py has no __version__ line.' }
    $Version = $Matches[1]
}
$packageVersion = "$Version.0"  # the Store requires the fourth part to be 0

if (-not $ToolsDir) {
    $sdk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'x64\makeappx.exe') } | Sort-Object Name | Select-Object -Last 1
    if (-not $sdk) { throw 'makeappx.exe not found: install the Windows SDK, or pass -ToolsDir (the Microsoft.Windows.SDK.BuildTools NuGet package has it).' }
    $ToolsDir = Join-Path $sdk.FullName 'x64'
}
$makeappx = Join-Path $ToolsDir 'makeappx.exe'

$layout = Join-Path $PSScriptRoot 'build\msix-layout'
if (Test-Path -LiteralPath $layout) { Remove-Item -LiteralPath $layout -Recurse -Force }
Write-Host '==> package layout'
Copy-Item -LiteralPath $app -Destination $layout -Recurse
& $python (Join-Path $repo 'scripts\make_icons.py') --msix (Join-Path $layout 'Assets')
if ($LASTEXITCODE -ne 0) { throw 'tile generation failed' }

function ConvertTo-XmlText([string]$Value) { return [Security.SecurityElement]::Escape($Value) }
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'msix\AppxManifest.xml') -Raw
$tokens = @{
    IDENTITY_NAME = $IdentityName; PUBLISHER = $Publisher; VERSION = $packageVersion
    DISPLAY_NAME = $DisplayName; PUBLISHER_DISPLAY_NAME = $PublisherDisplayName; DESCRIPTION = $Description
}
foreach ($k in $tokens.Keys) { $manifest = $manifest.Replace("{{$k}}", (ConvertTo-XmlText $tokens[$k])) }
if ($manifest -match '\{\{[A-Z_]+\}\}') { throw "Unfilled manifest token: $($Matches[0])" }
[IO.File]::WriteAllText((Join-Path $layout 'AppxManifest.xml'), $manifest, [Text.UTF8Encoding]::new($false))

$out = Join-Path $PSScriptRoot "dist\$IdentityName-$Version.msix"
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
Write-Host '==> makeappx pack'
& $makeappx pack /d $layout /p $out /o
if ($LASTEXITCODE -ne 0) { throw 'makeappx failed' }
Write-Host "==> package: $out"
if ($CertThumbprint) {
    & (Join-Path $ToolsDir 'signtool.exe') sign /fd SHA256 /sha1 $CertThumbprint $out
    if ($LASTEXITCODE -ne 0) { throw 'signtool failed' }
    Write-Host "    signed for testing; install with: Add-AppxPackage -Path `"$out`""
}
