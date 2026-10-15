# 停止 agentscop-platform 全部服务: FastAPI 后端 / Web UI (Vite) / Redis 等
# 用法:  .\stop-all.ps1

$ErrorActionPreference = "SilentlyContinue"
$Root = $PSScriptRoot
$LogDir = Join-Path $Root "logs"
$BackendPort = if ($env:AGENTS_BACKEND_PORT) { $env:AGENTS_BACKEND_PORT } else { "8020" }

# ---------- 1. Kill process trees recorded in PID files (top-level wrappers) ----------
# The stop flag tells backend-watchdog.ps1 to exit instead of restarting.
Set-Content (Join-Path $LogDir "stop.flag") (Get-Date -Format o)
foreach ($name in @("backend.pid", "webui.pid")) {
    $pf = Join-Path $LogDir $name
    if (Test-Path $pf) {
        $procId = (Get-Content $pf | Select-Object -First 1).Trim()
        if ($procId -match '^\d+$' -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
            Write-Host "[Stop] process tree (PID $procId, from $name)"
            taskkill /PID $procId /T /F | Out-Null
        }
        Remove-Item $pf -Force
    }
}

# ---------- 2. Fallback: kill whatever still listens on service ports ----------
function Stop-PortListener([int]$Port, [string]$Name) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    $procIds = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $procIds) {
        if ($procId -and $procId -ne 0) {
            Write-Host "[Stop] $Name still on port $Port (PID $procId)"
            taskkill /PID $procId /T /F | Out-Null
        }
    }
}

Stop-PortListener $BackendPort "FastAPI backend"
Stop-PortListener 5173 "Vite frontend"
Stop-PortListener 3000 "Express"

# ---------- 3. Stop Redis / Qdrant / TEI containers ----------
foreach ($svc in @(
    @{ Name = "Redis";  Container = "agentscope-redis" },
    @{ Name = "Qdrant"; Container = "agentscope-qdrant" },
    @{ Name = "TEI";    Container = "agentscope-tei" }
)) {
    $exists = docker ps -a --filter "name=$($svc.Container)" --format "{{.Names}}" 2>$null
    if ($exists -match $svc.Container) {
        $running = docker ps --filter "name=$($svc.Container)" --format "{{.Names}}" 2>$null
        if ($running -match $svc.Container) {
            docker stop $svc.Container | Out-Null
            Write-Host "[Stop] $($svc.Name) container: $($svc.Container)"
        }
    }
}

# ---------- 4. Verify ----------
Start-Sleep -Seconds 2
$left = 0
foreach ($port in @([int]$BackendPort, 5173, 3000, 6379, 6333, 19556)) {
    $up = (Test-NetConnection -ComputerName localhost -Port $port -WarningAction SilentlyContinue -InformationLevel Quiet)
    if ($up) { $left++ }
}
if ($left -eq 0) {
    Write-Host "All services stopped."
} else {
    Write-Host "Warning: $left port(s) still open, close manually if needed."
}
