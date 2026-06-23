@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3.11+ not found in PATH.
  exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo [ERROR] Python 3.11+ is required.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 exit /b 1
  echo [INFO] Installing dependencies into .venv...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -e .
  if errorlevel 1 (
    echo [ERROR] Dependency install failed.
    exit /b 1
  )
  echo [INFO] Installing Playwright browser...
  ".venv\Scripts\python.exe" -m playwright install chromium
  if errorlevel 1 (
    echo [WARN] Playwright browser install failed. Browser operations will not work until fixed.
  )
) else (
  echo [INFO] Virtual environment already exists, skipping install.
)

if not exist "data" mkdir data
if not exist "logs" mkdir logs
if not exist "runtime" mkdir runtime

echo [OK] Install finished. Run start.bat to launch agent-news.
exit /b 0
