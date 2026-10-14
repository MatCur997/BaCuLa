# Prepara la macchina BACKEND: ambiente Python, dipendenze, file .env e modello LLM.
# Uso:  .\setup.ps1            (oppure .\setup.ps1 -Python "py -3.12")
param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { Invoke-Expression "$Python -m venv .venv" }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path .env)) {
    $key = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
    (Get-Content .env.example) -replace '^API_KEY=.*', "API_KEY=$key" | Set-Content -Encoding utf8 .env
    Write-Host "`nCreato .env con una nuova API_KEY. Copiala nel .env di CHAT e VOICE:" -ForegroundColor Yellow
    Write-Host "  API_KEY=$key`n"
}

$model = (Select-String -Path .env -Pattern '^OLLAMA_MODEL=(.*)$').Matches[0].Groups[1].Value
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Scarico il modello $model (solo la prima volta)..."
    ollama pull $model
} else {
    Write-Warning "Ollama non trovato: installalo da https://ollama.com/download e poi esegui: ollama pull $model"
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -in 'Dhcp', 'Manual' -and $_.IPAddress -notlike '169.*' } | Select-Object -First 1).IPAddress
Write-Host "`nPronto. Indirizzo da mettere in BACKEND_URL su CHAT e VOICE: http://${ip}:8000" -ForegroundColor Green
Write-Host "Se le altre macchine non lo raggiungono, apri la porta (PowerShell come amministratore):"
Write-Host '  New-NetFirewallRule -DisplayName "BaCuLa backend" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private'
