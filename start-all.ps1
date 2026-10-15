# agentscop-platform 一键启动: Redis (Docker) + FastAPI 后端 + Web UI
# 所有服务后台运行, 日志/PID 写入 logs/
# 用法:  .\start-all.ps1        (在 agentscop-platform 目录下)
# 停止:  .\stop-all.ps1
#
# 本地默认后端端口 8020（本机 127.0.0.1:8000 常被 CodeBuddy 等 IDE 的
# 本地服务占用）；AGENTS_BACKEND_PORT 可覆盖。容器/服务器部署仍为 8000。

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$LogDir = Join-Path $Root "logs"
$VenvPython = Join-Path $Root "..\.venv\Scripts\python.exe"
$WebUiDir = Join-Path $Root "web_ui"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$BackendPort = if ($env:AGENTS_BACKEND_PORT) { $env:AGENTS_BACKEND_PORT } else { "8020" }
$env:AGENTS_BACKEND_PORT = $BackendPort

function Test-Port([int]$Port) {
    (Test-NetConnection -ComputerName localhost -Port $Port -WarningAction SilentlyContinue -InformationLevel Quiet)
}

# ---------- 1. Redis ----------
if (Test-Port 6379) {
    Write-Host "[Redis]   port 6379 already running, skip"
} else {
    docker start agentscope-redis 2>$null
    if (-not $?) {
        # Named volume + AOF: RDB-only lost recent writes across restarts
        docker run -d --name agentscope-redis -p 127.0.0.1:6379:6379 `
            -v agentscope_redis_data:/data `
            redis:7 redis-server --appendonly yes | Out-Null
    }
    Write-Host "[Redis]   started (docker container: agentscope-redis)"
}

# ---------- 2. Qdrant (knowledge base vector store) ----------
# Named volume for persistence (same lesson as Redis). Bound to
# 127.0.0.1 only: Qdrant has no auth and all user traffic goes
# through FastAPI, so it never needs LAN exposure.
if (Test-Port 6333) {
    Write-Host "[Qdrant]  port 6333 already running, skip"
} else {
    docker start agentscope-qdrant 2>$null
    if (-not $?) {
        docker run -d --name agentscope-qdrant -p 127.0.0.1:6333:6333 `
            -v agentscope_qdrant_data:/qdrant/storage `
            qdrant/qdrant:v1.19.1 | Out-Null
    }
    Write-Host "[Qdrant]  started (docker container: agentscope-qdrant)"
}

# ---------- 3. TEI (local embedding model bge-small-zh-v1.5) ----------
# OpenAI-compatible /v1/embeddings. Weights come from models/bge/
# (Git LFS), mounted read-only. Port 19556 bound to 127.0.0.1 only
# (TEI has no auth). First start needs a few seconds to load weights.
if (Test-Port 19556) {
    Write-Host "[TEI]     port 19556 already running, skip"
} else {
    docker start agentscope-tei 2>$null
    if (-not $?) {
        docker run -d --name agentscope-tei -p 127.0.0.1:19556:80 `
            -v "$Root\models\bge:/data:ro" `
            ghcr.io/huggingface/text-embeddings-inference:cpu-1.8 `
            --model-id /data/BAAI--bge-small-zh-v1.5 | Out-Null
    }
    Write-Host "[TEI]     started (docker container: agentscope-tei)"
}

# ---------- 4. FastAPI backend (watchdog + main.py) ----------
if (Test-Port $BackendPort) {
    Write-Host "[Backend] port $BackendPort already running, skip"
} else {
    # Dev frontend mode: FastAPI reverse-proxies non-API paths (incl.
    # the HMR WebSocket) to the Vite dev server, so the browser only
    # ever talks to the backend port. Local start is ALWAYS dev mode -
    # whether a dist/ build exists is irrelevant.
    $env:AGENTS_DEV_FRONTEND_URL = "http://localhost:5173"
    $p = Start-Process -FilePath "powershell" `
        -ArgumentList "-ExecutionPolicy", "Bypass", `
            "-File", (Join-Path $Root "backend-watchdog.ps1") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
    Set-Content (Join-Path $LogDir "backend.pid") $p.Id
    Write-Host "[Backend] watchdog starting (PID $($p.Id)) -> http://localhost:$BackendPort"
}

# ---------- 5. Web UI (Vite dev server only) ----------
# Frontend talks to FastAPI directly (client.ts getBaseUrl), so the
# vendored Express backend (:3000) is dead weight locally - only the
# Vite dev server is needed; FastAPI proxies it onto the backend port.
if (Test-Port 5173) {
    Write-Host "[WebUI]   port 5173 already running, skip"
} else {
    $pnpm = (Get-Command pnpm.cmd).Source
    $p = Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "`"$pnpm`" dev:frontend" `
        -WorkingDirectory $WebUiDir `
        -RedirectStandardOutput (Join-Path $LogDir "webui_stdout.log") `
        -RedirectStandardError (Join-Path $LogDir "webui_stderr.log") `
        -PassThru -WindowStyle Hidden
    Set-Content (Join-Path $LogDir "webui.pid") $p.Id
    Write-Host "[WebUI]   Vite starting (PID $($p.Id)) -> http://localhost:5173 (proxied via :$BackendPort)"
}

# ---------- 6. Health check ----------
Write-Host ""
Write-Host "Waiting for services ..."
$targets = @(
    @{ Name = "Redis    6379"; Port = 6379 },
    @{ Name = "Qdrant   6333"; Port = 6333 },
    @{ Name = "TEI    19556"; Port = 19556 },
    @{ Name = "FastAPI  $BackendPort"; Port = $BackendPort },
    @{ Name = "Vite     5173"; Port = 5173 }
)
$allOk = $true
foreach ($t in $targets) {
    $ok = $false
    for ($i = 0; $i -lt 45; $i++) {
        if (Test-Port $t.Port) { $ok = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ok) { $allOk = $false }
    $state = if ($ok) { "OK" } else { "FAILED (check logs\)" }
    Write-Host ("  {0}  {1}" -f $t.Name, $state)
}

Write-Host ""
if ($allOk) {
    Write-Host "All services are up."
} else {
    Write-Host "Some services failed. See logs\backend_stderr.log / logs\webui_stderr.log"
}
Write-Host "Web UI : http://localhost:$BackendPort  (single origin; frontend proxied to Vite dev on 5173)"
Write-Host "Stop   : .\stop-all.ps1"
