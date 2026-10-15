# 调试模式: 后端/前端各开一个可见控制台窗口, 实时看日志, Ctrl+C 停止
# 用法:
#   .\start-dev.ps1            # 可见窗口, 正常运行
#   .\start-dev.ps1 -DebugPy   # 后端等待 VSCode 调试器接入 (端口 5678)
# 停止: 各窗口内 Ctrl+C, 或 .\stop-all.ps1
#
# 本地默认后端端口 8020（本机 127.0.0.1:8000 常被 CodeBuddy 等 IDE 的
# 本地服务占用）。可用环境变量 AGENTS_BACKEND_PORT 覆盖；容器/服务器
# 部署仍为 8000（compose 与 deploy/）。

param(
    [switch]$DebugPy
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$Py = Join-Path $Root "..\.venv\Scripts\python.exe"
$WebUiDir = Join-Path $Root "web_ui"
$LogDir = Join-Path $Root "logs"
$BackendPort = if ($env:AGENTS_BACKEND_PORT) { $env:AGENTS_BACKEND_PORT } else { "8020" }
$env:AGENTS_BACKEND_PORT = $BackendPort
# 前端 DEV 模式直连后端端口（client.ts 读 VITE_API_PORT；默认 8000 供容器/标准环境）
$env:VITE_API_PORT = $BackendPort
# Vite 端口：5173 已被 Docker Desktop(::)/CodeBuddy(127.0.0.1)/wslrelay([::1])
# 三方常驻监听，Vite 绑不上——改用 5174，与后端反代目标保持一致。
$FrontendPort = if ($env:AGENTS_FRONTEND_PORT) { $env:AGENTS_FRONTEND_PORT } else { "5174" }

# ---------- FastAPI backend (visible window) ----------
# Dev frontend mode: FastAPI proxies non-API paths (incl. HMR WS) to
# Vite, so debugging happens on the backend port only. Always set.
# 用 [::1] 而非 localhost/127.0.0.1：本机 127.0.0.1:5173 可能被 IDE
# 本地服务抢占（重定向到其他端口），[::1] 恒达 Vite。
$env:AGENTS_DEV_FRONTEND_URL = "http://[::1]:$FrontendPort"
if (-not (Test-NetConnection -ComputerName localhost -Port $BackendPort -WarningAction SilentlyContinue -InformationLevel Quiet)) {
    Remove-Item (Join-Path $LogDir "stop.flag") -Force -ErrorAction SilentlyContinue
    if ($DebugPy) {
        # Backend blocks until the VSCode debugger attaches (port 5678)
        Start-Process -FilePath $Py `
            -ArgumentList "-m", "debugpy", "--listen", "5678", "--wait-for-client", "main.py" `
            -WorkingDirectory $Root
        Write-Host "[Backend] debugpy listening on 5678, waiting for VSCode attach..."
    } else {
        Start-Process -FilePath $Py -ArgumentList "main.py" -WorkingDirectory $Root
        Write-Host "[Backend] started in new window -> http://localhost:$BackendPort"
    }
} else {
    Write-Host "[Backend] port $BackendPort already running, skip"
}

# ---------- Frontend (visible window) ----------
# 直接调用 frontend/node_modules/.bin/vite.cmd，绕开 pnpm workspace
# （Windows 安全策略拦截 pnpm 生成的 junction，os error 448）。
if (-not (Test-NetConnection -ComputerName localhost -Port $FrontendPort -WarningAction SilentlyContinue -InformationLevel Quiet)) {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "cd /d `"$WebUiDir\frontend`" && node_modules\.bin\vite.cmd --port $FrontendPort --strictPort" -WorkingDirectory $WebUiDir
    Write-Host "[WebUI]   started in new window -> http://localhost:$FrontendPort"
} else {
    Write-Host "[WebUI]   port $FrontendPort already running, skip"
}

# ---------- Redis / Qdrant / TEI ----------
# 本地默认模式（sqlite + 本地向量库 + file 角色表）不需要下面三个容器；
# 这里仍提供一键拉起，便于切到生产模式验证（.env: AGENTS_STORAGE=redis）。
if (-not (Test-NetConnection -ComputerName localhost -Port 6379 -WarningAction SilentlyContinue -InformationLevel Quiet)) {
    docker start agentscope-redis 2>$null
    if (-not $?) { docker run -d --name agentscope-redis -p 127.0.0.1:6379:6379 redis:7 | Out-Null }
    Write-Host "[Redis]   started (docker: agentscope-redis)"
} else {
    Write-Host "[Redis]   port 6379 already running, skip"
}

if (-not (Test-NetConnection -ComputerName localhost -Port 6333 -WarningAction SilentlyContinue -InformationLevel Quiet)) {
    docker start agentscope-qdrant 2>$null
    if (-not $?) {
        docker run -d --name agentscope-qdrant -p 127.0.0.1:6333:6333 `
            -v agentscope_qdrant_data:/qdrant/storage `
            qdrant/qdrant:v1.19.1 | Out-Null
    }
    Write-Host "[Qdrant]  started (docker: agentscope-qdrant)"
} else {
    Write-Host "[Qdrant]  port 6333 already running, skip"
}

if (-not (Test-NetConnection -ComputerName localhost -Port 19556 -WarningAction SilentlyContinue -InformationLevel Quiet)) {
    docker start agentscope-tei 2>$null
    if (-not $?) {
        docker run -d --name agentscope-tei -p 127.0.0.1:19556:80 `
            -v "$Root\models\bge:/data:ro" `
            ghcr.io/huggingface/text-embeddings-inference:cpu-1.8 `
            --model-id /data/BAAI--bge-small-zh-v1.5 | Out-Null
    }
    Write-Host "[TEI]     started (docker: agentscope-tei)"
} else {
    Write-Host "[TEI]     port 19556 already running, skip"
}

Write-Host ""
Write-Host "Backend dir : agentscop-platform\server  (edit server\app.py to customize the service)"
Write-Host "Frontend dir: agentscop-platform\web_ui\frontend (Vite hot-reload enabled)"
Write-Host "Web UI      : http://localhost:$BackendPort  (frontend proxied to Vite dev on 5173)"
