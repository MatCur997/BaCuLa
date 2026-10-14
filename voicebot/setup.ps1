# Prepara la macchina VOICE: dipendenze, voce italiana (Piper) e modello di riconoscimento vocale (Whisper).
# Uso:  .\setup.ps1          (solo CPU)
#       .\setup.ps1 -Gpu     (aggiunge le librerie CUDA per usare una GPU NVIDIA: riconoscimento molto più veloce)
param([string]$Python = "python", [switch]$Gpu)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { Invoke-Expression "$Python -m venv .venv" }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt
if ($Gpu) { & $py -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10" }

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "`nCreato .env: imposta BACKEND_URL (IP del backend) e API_KEY (la stessa del backend)." -ForegroundColor Yellow
}

Write-Host "Scarico la voce italiana (Piper)..."
& $py -m piper.download_voices --download-dir models it_IT-paola-medium

$whisper = (Select-String -Path .env -Pattern '^WHISPER_MODEL=(.*)$').Matches[0].Groups[1].Value
Write-Host "Scarico il modello Whisper '$whisper'..."
& $py -c "from faster_whisper import WhisperModel; WhisperModel('$whisper', device='cpu', compute_type='int8')"

Write-Host "Pronto. Avvia con .\run.ps1" -ForegroundColor Green
