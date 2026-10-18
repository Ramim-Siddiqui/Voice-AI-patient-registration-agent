@echo off
REM Starts the API + dashboard locally on http://127.0.0.1:8000 (SQLite, demo data).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
if not exist ".env" copy .env.example .env >nul
echo.
echo Dashboard: http://127.0.0.1:8000/     API docs: http://127.0.0.1:8000/docs
echo Press Ctrl+C to stop.
echo.
.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
