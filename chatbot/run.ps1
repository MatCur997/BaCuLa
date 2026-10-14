# Avvia il chat-bot. Poi apri http://localhost:8501 nel browser di questa macchina.
Set-Location $PSScriptRoot
Write-Host "Apri http://localhost:8501 nel browser" -ForegroundColor Green
& .\.venv\Scripts\python.exe -m streamlit run app.py
