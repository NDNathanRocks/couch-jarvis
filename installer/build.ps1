<#
.SYNOPSIS
    Builds CouchJarvis.exe (PyInstaller) and, when Inno Setup 6 is installed, CouchJarvis-Setup.exe.

.DESCRIPTION
    1. builds the phone app (web-remote) so the PC can also serve it on the LAN
    2. writes installer\defaults.env from the relay/PWA URLs (parameters, else your repo .env)
    3. freezes pc-daemon with PyInstaller into installer\dist\CouchJarvis\
    4. zips a portable copy, and compiles the installer if ISCC.exe is available

    The defaults.env baked into a build holds only public endpoints, never secrets. Each
    install generates its own room, secret and keys on first run.

.PARAMETER RelayUrl
    wss://... WebSocket URL from `sam deploy`. Default: CJ_RELAY_WS_URL from the repo .env.

.PARAMETER PwaUrl
    https://... where the phone app is hosted. Default: CJ_PWA_BASE_URL from the repo .env.

.PARAMETER Version
    Version stamped into the installer. Default: __version__ in pc-daemon\version.py (the one source).

.PARAMETER SkipWeb
    Reuse the existing web-remote\dist instead of rebuilding it.

.EXAMPLE
    .\installer\build.ps1
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Interactive console tool: output is for a human, not the pipeline.')]
[CmdletBinding()]
param(
    [string]$RelayUrl,
    [string]$PwaUrl,
    [string]$Version,
    [switch]$SkipWeb
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
if (-not $Version) {
    $versionSource = Get-Content -LiteralPath (Join-Path $repo 'pc-daemon\version.py') -Raw
    if ($versionSource -notmatch '__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') {
        throw 'pc-daemon\version.py has no __version__ = "X.Y.Z" line.'
    }
    $Version = $Matches[1]
}
$python = Join-Path $repo '.venv\Scripts\python.exe'
$dist = Join-Path $PSScriptRoot 'dist'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python venv not found at $python. Create it first: uv venv --python 3.12 .venv ; uv pip install -r requirements.txt"
}

function Read-DotEnv {
    param([string]$Path)
    $values = @{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$' -and -not $line.TrimStart().StartsWith('#')) {
                $values[$Matches[1]] = $Matches[2].Trim('"')
            }
        }
    }
    return $values
}

$env_ = Read-DotEnv (Join-Path $repo '.env')
if (-not $RelayUrl) { $RelayUrl = $env_['CJ_RELAY_WS_URL'] }
if (-not $PwaUrl) { $PwaUrl = $env_['CJ_PWA_BASE_URL'] }
if (-not $RelayUrl -or -not $PwaUrl) {
    throw 'Pass -RelayUrl and -PwaUrl (or set CJ_RELAY_WS_URL and CJ_PWA_BASE_URL in the repo .env).'
}
if ($RelayUrl -notmatch '^wss://') { throw "RelayUrl must start with wss:// (got $RelayUrl)" }
if ($PwaUrl -notmatch '^https://') { throw "PwaUrl must start with https:// (got $PwaUrl)" }

Write-Host "==> relay $RelayUrl"
Write-Host "==> phone app $PwaUrl"

if (-not $SkipWeb) {
    Write-Host '==> building the phone app'
    Push-Location (Join-Path $repo 'web-remote')
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
        $env:VITE_RELAY_WS_URL = $RelayUrl
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'web build failed' }
    } finally { Pop-Location }
}

$defaults = @(
    '# Couch Jarvis settings (edit, then restart the app). Created on first run.'
    "CJ_RELAY_WS_URL=$RelayUrl"
    "CJ_PWA_BASE_URL=$PwaUrl"
    '# Speech-to-text: auto uses an NVIDIA GPU when there is one, otherwise the CPU.'
    'CJ_STT_DEVICE=auto'
    '# CJ_STT_MODEL=small          speech model (default: auto = large-v3-turbo on a GPU, small on a CPU)'
    '# CJ_GEMINI_API_KEY=...      free key from aistudio.google.com/apikey: fast cloud understanding of commands'
    '# CJ_GROQ_API_KEY=gsk_...     free-tier cloud speech-to-text (no GPU needed)'
    '# CJ_REMOTE_INPUT=true        false = phones cannot move the mouse or type'
)
Set-Content -LiteralPath (Join-Path $PSScriptRoot 'defaults.env') -Value $defaults -Encoding utf8

Write-Host '==> installing build tools'
uv pip install -r (Join-Path $repo 'requirements-build.txt') --python $python
if ($LASTEXITCODE -ne 0) { throw 'could not install build requirements' }

Write-Host '==> freezing the daemon (this takes a few minutes)'
& $python -m PyInstaller --noconfirm --clean `
    --distpath $dist --workpath (Join-Path $PSScriptRoot 'build') (Join-Path $PSScriptRoot 'couch_jarvis.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

function Assert-NoSecrets {
    # Fail the build if this machine's API keys (CJ_GROQ_API_KEY, CJ_GEMINI_API_KEY, ...) ended up
    # in the app, or if any settings file was bundled. Users add their own keys after installing.
    param([string]$Dir)
    $bundled = Get-ChildItem -LiteralPath $Dir -Recurse -File -Force |
        Where-Object { $_.Name -in '.env', 'config.env', 'room.json' -or $_.Name -like '.env.*' }
    if ($bundled) { throw "Settings file(s) bundled into the app: $($bundled.FullName -join ', ')" }
    $secrets = @{}
    foreach ($name in 'CJ_GROQ_API_KEY', 'CJ_GEMINI_API_KEY') {
        if ($env_.ContainsKey($name) -and $env_[$name].Length -ge 8) { $secrets[$name] = $env_[$name] }
    }
    if ($secrets.Count -eq 0) { return }
    $binary = '.dll', '.pyd', '.exe', '.png', '.ico', '.woff2', '.node', '.zip'
    $files = Get-ChildItem -LiteralPath $Dir -Recurse -File |
        Where-Object { $binary -notcontains $_.Extension.ToLower() -and $_.Length -lt 20MB }
    foreach ($file in $files) {
        foreach ($name in $secrets.Keys) {
            if (Select-String -LiteralPath $file.FullName -Pattern $secrets[$name] -SimpleMatch -Quiet) {
                throw "$name was found inside the build ($($file.FullName)). Refusing to ship it."
            }
        }
    }
    Write-Host '==> secret scan: no API key or settings file in the build'
}

$app = Join-Path $dist 'CouchJarvis'
Assert-NoSecrets $app
$size = (Get-ChildItem -LiteralPath $app -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ('==> built {0} ({1:N0} MB)' -f $app, $size)

$zip = Join-Path $dist "CouchJarvis-portable-$Version.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
# Antivirus often scans fresh binaries and briefly locks them: retry instead of failing the build.
for ($attempt = 1; $attempt -le 5; $attempt++) {
    try {
        [IO.Compression.ZipFile]::CreateFromDirectory($app, $zip, [IO.Compression.CompressionLevel]::Optimal, $true)
        break
    } catch {
        if ($attempt -eq 5) { throw }
        if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
        Write-Host "zip attempt $attempt failed ($($_.Exception.Message)); retrying in 10 s"
        Start-Sleep -Seconds 10
    }
}
Write-Host "==> portable copy: $zip"

$iscc = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

if ($iscc) {
    Write-Host '==> compiling the installer'
    & $iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot 'CouchJarvis.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
    Write-Host "==> installer: $(Join-Path $dist 'CouchJarvis-Setup.exe')"
} else {
    Write-Warning 'Inno Setup 6 was not found, so CouchJarvis-Setup.exe was not built.'
    Write-Warning 'Install it with:  winget install JRSoftware.InnoSetup   then run this script again.'
}
