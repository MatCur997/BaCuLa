# Prepara la macchina CHAT. Uso:  .\setup.ps1
param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { Invoke-Expression "$Python -m venv .venv" }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "`nCreato .env: imposta BACKEND_URL (IP del backend) e API_KEY (la stessa del backend)." -ForegroundColor Yellow
}
Write-Host "Pronto. Avvia con .\run.ps1" -ForegroundColor Green
