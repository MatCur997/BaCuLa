# Prepara la macchina BACKEND: ambiente Python, dipendenze, file .env e modello LLM.
# Uso:  .\setup.ps1            (oppure .\setup.ps1 -Python "py -3.12")
param([string]$Python = "python")
# pip e ollama scrivono messaggi normali su stderr: l'esito si controlla col codice d'uscita.
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
    $key = & $py -c "import secrets; print(secrets.token_urlsafe(32))"
    (Get-Content .env.example) -replace '^API_KEY=.*', "API_KEY=$key" | Set-Content -Encoding utf8 .env
    Write-Host "`nCreato .env con una nuova API_KEY. Copiala nel .env di CHAT e VOICE:" -ForegroundColor Yellow
    Write-Host "  API_KEY=$key`n"
}

$match = Select-String -Path .env -Pattern '^OLLAMA_MODEL=(.+)$'
$model = if ($match) { $match.Matches[0].Groups[1].Value.Trim() } else { "gemma4:latest" }
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Scarico il modello $model (solo la prima volta)..."
    Step "download modello $model" { ollama pull $model }
} else {
    Write-Warning "Ollama non trovato: installalo da https://ollama.com/download e poi esegui: ollama pull $model"
}

# La scheda con il gateway predefinito è quella collegata alla rete reale
# (esclude le schede virtuali di VirtualBox/Hyper-V/VPN, es. 192.168.56.1).
$ips = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } |
         ForEach-Object { $_.IPv4Address.IPAddress })
Write-Host "`nPronto. Indirizzo da mettere in BACKEND_URL su CHAT e VOICE:" -ForegroundColor Green
foreach ($ip in $ips) { Write-Host "  http://${ip}:8000" -ForegroundColor Green }
if ($ips.Count -ne 1) { Write-Host "  (usa l'indirizzo della rete a cui sono collegati anche CHAT e VOICE; verifica con: ipconfig)" }
Write-Host "Se le altre macchine non lo raggiungono, apri la porta (PowerShell come amministratore):"
# Profile Any: le reti Wi-Fi sono spesso classificate "Pubbliche"; RemoteAddress limita l'accesso alla rete locale.
Write-Host '  New-NetFirewallRule -DisplayName "BaCuLa backend" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any -RemoteAddress LocalSubnet'
