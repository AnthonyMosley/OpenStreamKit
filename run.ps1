# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Stop any running uvicorn servers (dev-safe)
Get-Process uvicorn -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force

# Start server
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
