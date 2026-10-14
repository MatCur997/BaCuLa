# Avvia il chat-bot e apre il browser su http://localhost:8501
Set-Location $PSScriptRoot
& .\.venv\Scripts\python.exe -m streamlit run app.py
