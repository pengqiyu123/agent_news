$ErrorActionPreference = "Continue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$venvPython = Join-Path $appRoot ".venv\Scripts\python.exe"
$dataDir = Join-Path $appRoot "data"

Write-Host "[INFO] agent-news doctor"
Write-Host ""

# Python
if (Get-Command python -ErrorAction SilentlyContinue) {
    Write-Host "[ OK ] Python found"
} else {
    Write-Host "[FAIL] Python not found in PATH"
}

# Virtual environment
if (Test-Path $venvPython) {
    Write-Host "[ OK ] Virtual environment exists (.venv)"
} else {
    Write-Host "[FAIL] Virtual environment missing. Run install.bat first."
}

# Data dir
if (Test-Path $dataDir) {
    Write-Host "[ OK ] Data directory exists"
} else {
    Write-Host "[WARN] Data directory missing (will be created on first start)"
}

# Backend reachable?
try {
    $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3
    Write-Host "[ OK ] Backend reachable: version $($response.version)"
} catch {
    Write-Host "[WARN] Backend not reachable. Run start.bat to launch it."
}
