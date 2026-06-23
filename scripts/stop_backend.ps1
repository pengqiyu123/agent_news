$ErrorActionPreference = "Stop"

function Get-ProjectBackendProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "python.exe" -and ([string]$_.CommandLine) -like "*agent_news.main:app*"
    })
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$pidFile = Join-Path $appRoot "runtime\logs\backend.pid"

$projectBackends = @(Get-ProjectBackendProcesses)
if ($projectBackends.Count -eq 0) {
    Write-Host "[INFO] No agent-news backend is running."
} else {
    foreach ($proc in $projectBackends) {
        Write-Host "[INFO] Stopping agent-news backend PID=$($proc.ProcessId)"
        Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Write-Host "[OK] agent-news backend stopped."
}

if (Test-Path $pidFile) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
exit 0
