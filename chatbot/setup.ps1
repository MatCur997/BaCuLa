# Prepara la macchina CHAT. Uso:  .\setup.ps1
param([string]$Python = "python")
# pip scrive messaggi normali su stderr: l'esito si controlla col codice d'uscita.
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

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "`nCreato .env: imposta BACKEND_URL (IP del backend) e API_KEY (la stessa del backend)." -ForegroundColor Yellow
}
Write-Host "`nPronto. Avvia con .\run.ps1" -ForegroundColor Green
