# Avvia il voice-bot. Poi apri http://localhost:8002 su QUESTA macchina
# (il browser concede il microfono solo a localhost o a siti HTTPS).
Set-Location $PSScriptRoot
Write-Host "Caricamento modelli... poi apri http://localhost:8002" -ForegroundColor Green
& .\.venv\Scripts\python.exe server.py
