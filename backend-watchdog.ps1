# Backend watchdog: keep main.py serving, not just running.
#
# Two failure modes are covered:
# 1. Process death — relaunch after 3s (previous behaviour).
# 2. Deaf listener — a Windows IOCP proactor bug kills the accept loop
#    on a client reset ("Accept failed on a socket", OSError 64 /
#    WinError 64 in proactor_events.py): the process stays alive but
#    never accepts another connection. Health supervision detects
#    ~40s of connection failures after a previously healthy start and
#    kills the child so it gets relaunched. In-flight indexing is
#    self-healing: documents stuck in pending/indexing are re-enqueued
#    by the framework's IndexSweeper after the lease expires.
# See docs/design.md section 14.3.
#
# Launched by start-all.ps1 (PID file = watchdog PID). stop-all.ps1
# writes logs\stop.flag before killing, so this loop exits instead of
# fighting the shutdown.

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$LogDir = Join-Path $Root "logs"
$Py = Join-Path $Root "..\.venv\Scripts\python.exe"
$StopFlag = Join-Path $LogDir "stop.flag"
Remove-Item $StopFlag -Force -ErrorAction SilentlyContinue
# 与 start-all.ps1 保持一致（环境变量继承；本机默认 8020）
$BackendPort = if ($env:AGENTS_BACKEND_PORT) { $env:AGENTS_BACKEND_PORT } else { "8020" }
$env:AGENTS_BACKEND_PORT = $BackendPort

function Test-BackendAlive {
    # Any HTTP response (200/401/...) proves the accept loop works;
    # the deaf mode answers nothing at all. /health requires Basic
    # auth, hence the 401-is-fine semantics.
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$BackendPort/health" `
            -UseBasicParsing -TimeoutSec 10 | Out-Null
        return $true
    } catch {
        if ($_.Exception.Response) { return $true }
        return $false
    }
}

while ($true) {
    if (Test-Path $StopFlag) { break }
    $p = Start-Process -FilePath $Py `
        -ArgumentList "main.py" `
        -WorkingDirectory $Root `
        -RedirectStandardOutput (Join-Path $LogDir "backend_stdout.log") `
        -RedirectStandardError (Join-Path $LogDir "backend_stderr.log") `
        -PassThru -WindowStyle Hidden
    Write-Host "[watchdog] backend PID $($p.Id)"

    $everOk = $false
    $fails = 0
    while (-not $p.HasExited) {
        Start-Sleep -Seconds 5
        if ($p.HasExited -or (Test-Path $StopFlag)) { break }
        if (Test-BackendAlive) {
            $everOk = $true
            $fails = 0
        } elseif ($everOk) {
            # skip failures before the first success: the backend is
            # still booting (uvicorn binds only after lifespan startup)
            $fails++
            Write-Host "[watchdog] health check failed ($fails/4)"
            if ($fails -ge 4) {
                Write-Host "[watchdog] backend deaf ~40s, killing to restart"
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
                break
            }
        }
    }

    if (Test-Path $StopFlag) { break }
    # Preserve the crashed run's logs before the next attempt overwrites them.
    Move-Item -Force (Join-Path $LogDir "backend_stderr.log") `
        (Join-Path $LogDir "backend_stderr.prev.log") -ErrorAction SilentlyContinue
    Move-Item -Force (Join-Path $LogDir "backend_stdout.log") `
        (Join-Path $LogDir "backend_stdout.prev.log") -ErrorAction SilentlyContinue
    Write-Host "[watchdog] backend exited, restarting in 3s..."
    Start-Sleep -Seconds 3
}
Write-Host "[watchdog] stop flag seen, exiting."
