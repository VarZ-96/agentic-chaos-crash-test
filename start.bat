@echo off
echo Starting Agentic Chaos Crash Test...
if not exist "venv\Scripts\activate.bat" (
    echo Virtual environment not found. Please create one using 'python -m venv venv' and install requirements.
    exit /b 1
)
call venv\Scripts\activate.bat
uvicorn main:app --port 8080 --reload
