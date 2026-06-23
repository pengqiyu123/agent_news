$ErrorActionPreference = "Stop"

# ── Helpers ─────────────────────────────────────────────────────────────────
function Get-ListenerPid {
    try {
        $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($listener) {
            return [int]$listener.OwningProcess
        }
    } catch {
    }
    return $null
}

function Get-ProcessInfo([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
}

function Is-ProjectBackend($ProcessInfo) {
    if (-not $ProcessInfo) { return $false }
    return ([string]$ProcessInfo.CommandLine) -like "*agent_news.main:app*"
}

function Is-BackedByVenv($ProcessInfo, [string]$VenvPythonPath) {
    if (-not $ProcessInfo) { return $false }
    $exePath = [string]$ProcessInfo.ExecutablePath
    if ($exePath -and $exePath -ieq $VenvPythonPath) { return $true }
    return $false
}

function Get-ProjectBackendProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "python.exe" -and ([string]$_.CommandLine) -like "*agent_news.main:app*"
    })
}

function Stop-ProjectBackendProcesses {
    $projectBackends = @(Get-ProjectBackendProcesses)
    foreach ($proc in $projectBackends) {
        Write-Host "[WARN] Stopping project backend PID=$($proc.ProcessId)"
        Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction SilentlyContinue
    }
    if ($projectBackends.Count -gt 0) { Start-Sleep -Seconds 2 }
}

# ── Paths ───────────────────────────────────────────────────────────────────
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$venvPython = Join-Path $appRoot ".venv\Scripts\python.exe"
$runtimeDir = Join-Path $appRoot "runtime"
$logDir = Join-Path $runtimeDir "logs"
$pidFile = Join-Path $logDir "backend.pid"
$startLock = Join-Path $logDir "backend.start.lock"
$outLog = Join-Path $logDir "backend.out.log"
$errLog = Join-Path $logDir "backend.err.log"
$appUrl = "http://127.0.0.1:8000"

# ── Lock helpers (prevent double-launch during the ~45s startup window) ─────
function Test-StartLockAlive {
    if (-not (Test-Path $startLock)) { return $false }
    try {
        $lockPid = [int](Get-Content $startLock -ErrorAction Stop | Select-Object -First 1)
        if ($lockPid -and (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) {
            return $true
        }
    } catch {}
    return $false
}
function Write-StartLock {
    "$PID" | Set-Content -Path $startLock -ErrorAction SilentlyContinue
}
function Remove-StartLock {
    if (Test-Path $startLock) { Remove-Item -LiteralPath $startLock -Force -ErrorAction SilentlyContinue }
}

if (-not (Test-Path $runtimeDir)) { New-Item -ItemType Directory -Path $runtimeDir | Out-Null }
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# ── Preconditions ───────────────────────────────────────────────────────────
if (-not (Test-Path $venvPython)) {
    Write-Host "[ERROR] Virtual environment not found: $venvPython"
    Write-Host "Run install.bat first."
    exit 1
}

# ── Start-lock guard (prevents two concurrent start scripts double-launching) ─
if (Test-StartLockAlive) {
    $lockPid = (Get-Content $startLock -ErrorAction SilentlyContinue | Select-Object -First 1)
    Write-Host "[INFO] Another start is in progress (lock PID=$lockPid). Waiting for it..."
    # Wait up to 60s for the other start to finish, then re-check the service.
    for ($i = 0; $i -lt 60; $i++) {
        if (-not (Test-StartLockAlive)) { break }
        try {
            $r = Invoke-WebRequest -UseBasicParsing "$appUrl/api/health" -TimeoutSec 2
            if ($r.StatusCode -eq 200) {
                Write-Host "[INFO] Backend is up (started by the other process). Exiting."
                exit 0
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    # Lock still alive after wait — stale? Remove and proceed.
    Remove-StartLock
}
Remove-StartLock  # clean any stale lock from a crashed prior run
Write-StartLock

# ── Already-running check ───────────────────────────────────────────────────
$projectBackends = @(Get-ProjectBackendProcesses)
$healthyVenvBackend = $null
$staleBackends = @()
foreach ($proc in $projectBackends) {
    if (Is-BackedByVenv $proc $venvPython) {
        if (-not $healthyVenvBackend) { $healthyVenvBackend = $proc } else { $staleBackends += $proc }
    } else {
        $staleBackends += $proc
    }
}
foreach ($proc in $staleBackends) {
    Write-Host "[WARN] Stopping stale project backend PID=$($proc.ProcessId)"
    Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction SilentlyContinue
}
if ($staleBackends.Count -gt 0) { Start-Sleep -Seconds 2 }

if ($healthyVenvBackend) {
    $listenerPid = Get-ListenerPid
    if ($listenerPid -and $listenerPid -eq [int]$healthyVenvBackend.ProcessId) {
        Set-Content -Path $pidFile -Value $listenerPid
        Write-Host "[INFO] agent-news is already running. PID=$listenerPid"
        Write-Host "URL: $appUrl"
        Start-Process $appUrl | Out-Null
        exit 0
    }
}

# ── Port conflict check ────────────────────────────────────────────────────
$listenerPid = Get-ListenerPid
if ($listenerPid) {
    $listenerProc = Get-ProcessInfo -ProcessId $listenerPid
    if (Is-ProjectBackend $listenerProc) {
        if (Is-BackedByVenv $listenerProc $venvPython) {
            Set-Content -Path $pidFile -Value $listenerPid
            Write-Host "[INFO] agent-news is already running. PID=$listenerPid"
            Write-Host "URL: $appUrl"
            Start-Process $appUrl | Out-Null
            exit 0
        }
        Write-Host "[WARN] Detected stale agent-news backend on port 8000. PID=$listenerPid"
        Write-Host "[INFO] Stopping stale process and restarting with project .venv..."
        Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    } else {
        Write-Host "[ERROR] Port 8000 is occupied by another process. PID=$listenerPid"
        Write-Host "[ERROR] Please free port 8000 first, then start agent-news again."
        exit 1
    }
}

if (Test-Path $pidFile) { Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue }

# ── Launch ──────────────────────────────────────────────────────────────────
Write-Host "[INFO] Starting agent-news..."

try {
    $process = Start-Process -FilePath $venvPython `
        -ArgumentList @("-m", "uvicorn", "agent_news.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory $appRoot `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru `
        -WindowStyle Hidden

    # Write the launched PID immediately as a fallback (don't wait for listener discovery).
    if ($process -and $process.Id) {
        Set-Content -Path $pidFile -Value $process.Id -ErrorAction SilentlyContinue
    }

    # ── Health-check polling ────────────────────────────────────────────────
    $ready = $false
    for ($i = 0; $i -lt 45; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing "$appUrl/api/health" -TimeoutSec 2
            $currentListenerPid = Get-ListenerPid
            $currentListenerProc = if ($currentListenerPid) { Get-ProcessInfo -ProcessId $currentListenerPid } else { $null }
            if (
                ($response.StatusCode -eq 200) -and
                [bool]$currentListenerPid -and
                (Is-ProjectBackend $currentListenerProc) -and
                (Is-BackedByVenv $currentListenerProc $venvPython)
            ) {
                $ready = $true
                break
            }
        } catch {
        }
        Start-Sleep -Seconds 1
    }

    if (-not $ready) {
        Stop-ProjectBackendProcesses
        if (Test-Path $pidFile) { Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue }
        Write-Host "[ERROR] Failed to detect backend listener on port 8000."
        if (Test-Path $errLog) {
            Write-Host "[ERROR] Backend stderr tail:"
            Get-Content -Path $errLog -Tail 20 -ErrorAction SilentlyContinue
        }
        exit 1
    }

    $listenerPid = Get-ListenerPid
    if ($listenerPid) {
        Set-Content -Path $pidFile -Value $listenerPid
    }

    Write-Host ""
    Write-Host "========================================"
    Write-Host "  agent-news started successfully"
    Write-Host "========================================"
    Write-Host "  PID:  $listenerPid"
    Write-Host "  URL:  $appUrl"
    Write-Host "  Logs: $logDir\"
    Write-Host "========================================"
    Write-Host ""

    Start-Process $appUrl | Out-Null
    exit 0
} finally {
    # Always release the start-lock, whether we succeeded, failed, or were interrupted.
    Remove-StartLock
}
