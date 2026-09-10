Write-Host "Starting Agentic Chaos Crash Test..." -ForegroundColor Green
if (!(Test-Path "venv\Scripts\activate.ps1")) {
    Write-Host "Virtual environment not found. Please create one using 'python -m venv venv' and install requirements." -ForegroundColor Red
    exit 1
}
& .\venv\Scripts\activate.ps1
uvicorn main:app --port 8080 --reload
