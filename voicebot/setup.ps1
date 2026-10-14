# Prepara la macchina VOICE: dipendenze, voce italiana (Piper) e modello di riconoscimento vocale (Whisper).
# Uso:  .\setup.ps1          (solo CPU)
#       .\setup.ps1 -Gpu     (aggiunge le librerie CUDA per usare una GPU NVIDIA: riconoscimento molto più veloce)
param([string]$Python = "python", [switch]$Gpu)
# pip, Piper e Whisper scrivono messaggi normali su stderr: l'esito si controlla col codice d'uscita.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Step([string]$What, [scriptblock]$Cmd) {
    & $Cmd
    if ($LASTEXITCODE -ne 0) { Write-Host "`nERRORE durante: $What" -ForegroundColor Red; exit 1 }
}

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Step "creazione ambiente Python" { Invoke-Expression "$Python -m venv .venv" } }
Step "aggiornamento pip" { & $py -m pip install --upgrade pip }
Step "installazione librerie" { & $py -m pip install -r requirements.txt }
if ($Gpu) { Step "installazione librerie CUDA" { & $py -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10" } }

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "`nCreato .env: imposta BACKEND_URL (IP del backend) e API_KEY (la stessa del backend)." -ForegroundColor Yellow
}

Write-Host "Scarico la voce italiana (Piper)..."
Step "download voce Piper" { & $py -m piper.download_voices --download-dir models it_IT-paola-medium }

$match = Select-String -Path .env -Pattern '^WHISPER_MODEL=(.+)$'
$whisper = if ($match) { $match.Matches[0].Groups[1].Value.Trim() } else { "small" }
Write-Host "Scarico il modello Whisper '$whisper'..."
Step "download modello Whisper" { & $py -c "from faster_whisper import WhisperModel; WhisperModel('$whisper', device='cpu', compute_type='int8')" }

Write-Host "`nPronto. Avvia con .\run.ps1" -ForegroundColor Green
