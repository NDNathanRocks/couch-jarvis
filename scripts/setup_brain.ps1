<#
.SYNOPSIS
    Sets up Couch Jarvis's local AI brain: installs Ollama if needed and downloads a model that fits this PC.

.DESCRIPTION
    Free-form commands ("put on the one with the cat", "search X and play a song featuring Y") are
    understood by a language model. Without a cloud key it runs locally through Ollama. This script:
      1. installs Ollama (per-user, no administrator rights) when it is missing,
      2. starts it,
      3. downloads qwen2.5:7b on a PC with an NVIDIA GPU, or the smaller qwen2.5:3b on a plain CPU,
      4. with a GPU, also downloads qwen2.5vl:7b, which finds buttons by looking at the screen.
    Re-running it is safe: finished downloads are skipped.

.PARAMETER Pause
    Wait for Enter before closing the window (for the Start-menu shortcut).

.PARAMETER SkipVision
    Do not download the screen-reading model even with a GPU.
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Interactive console tool: output is for a human, not the pipeline.')]
[CmdletBinding()]
param(
    [switch]$Pause,
    [switch]$SkipVision
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Find-Ollama {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
    )
    foreach ($path in $candidates) { if (Test-Path -LiteralPath $path) { return $path } }
    $found = Get-Command ollama -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    return $null
}

function Test-OllamaUp {
    try {
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
        return $true
    } catch { return $false }
}

function Test-NvidiaGpu {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { return $true }
    return (Test-Path -LiteralPath (Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'))
}

function Close-Window {
    if ($Pause) { Read-Host 'Press Enter to close' | Out-Null }
}

try {
    $gpu = Test-NvidiaGpu
    $textModel = if ($gpu) { 'qwen2.5:7b' } else { 'qwen2.5:3b' }
    Write-Host "This PC has $(if ($gpu) { 'an NVIDIA GPU' } else { 'no NVIDIA GPU' }): using $textModel."

    $ollama = Find-Ollama
    if (-not $ollama) {
        Write-Host '==> downloading Ollama (about 1 GB)'
        $installer = Join-Path $env:TEMP 'OllamaSetup.exe'
        Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile $installer -UseBasicParsing
        Write-Host '==> installing Ollama'
        $proc = Start-Process -FilePath $installer -ArgumentList '/SILENT' -Wait -PassThru
        if ($proc.ExitCode -ne 0) { throw "The Ollama installer failed (exit code $($proc.ExitCode))." }
        $ollama = Find-Ollama
        if (-not $ollama) { throw 'Ollama was installed but ollama.exe could not be found.' }
    }
    Write-Host "==> using $ollama"

    if (-not (Test-OllamaUp)) {
        Write-Host '==> starting Ollama'
        Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
        $deadline = (Get-Date).AddSeconds(40)
        while (-not (Test-OllamaUp) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
        if (-not (Test-OllamaUp)) { throw 'Ollama did not start.' }
    }

    $models = @($textModel)
    if ($gpu -and -not $SkipVision) { $models += 'qwen2.5vl:7b' }
    foreach ($model in $models) {
        Write-Host "==> downloading $model (this can take several minutes)"
        $ErrorActionPreference = 'Continue'  # ollama prints its progress bar on stderr: not an error
        & $ollama pull $model
        $code = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($code -ne 0) { throw "Downloading $model failed." }
    }

    Write-Host ''
    Write-Host 'Done. The brain is ready: restart Couch Jarvis (or just try a command).'
    Close-Window
} catch {
    Write-Host ''
    Write-Host "Could not finish: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'You can run "Set up AI brain" from the Start menu again, or add a free Gemini key instead:'
    Write-Host '  https://aistudio.google.com/apikey  ->  put CJ_GEMINI_API_KEY=... in Couch Jarvis settings.'
    Close-Window
    exit 1
}
