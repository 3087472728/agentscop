# agentscop-platform server image (multi-stage).
#
# Build (from the agentscop-platform root, BuildKit required):
#   docker build -f deploy/Dockerfile -t <registry>/agentscope/agentscop-platform:<tag> .
#
# Stage 1 builds the vendored Web UI (Vite) with pnpm.
# Stage 2 is the Python 3.13 runtime plus a Node.js runtime: the default
# workspace MCP (browser-use) spawns `npx @playwright/mcp@latest` as a
# stdio subprocess, which needs node/npx inside the container.
#
# BGE weights are NOT baked in: the TEI container mounts them read-only
# from the host (deploy/docker-compose.server.yml), so the image stays small.

# ---------- Stage 1: Web UI build ----------
FROM node:22-alpine AS frontend-build

# China-network default; override with --build-arg if needed.
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV npm_config_registry=${NPM_REGISTRY}

RUN corepack enable
WORKDIR /build/web_ui

# manifests first for layer caching, then the sources
COPY web_ui/pnpm-workspace.yaml web_ui/pnpm-lock.yaml web_ui/package.json ./
COPY web_ui/frontend/package.json ./frontend/
RUN pnpm fetch

COPY web_ui/ ./
RUN pnpm install --frozen-lockfile --offline && pnpm --filter frontend build
# output: /build/web_ui/frontend/dist

# ---------- Stage 2: runtime ----------
FROM python:3.13-slim

ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV npm_config_registry=${NPM_REGISTRY}

# Node.js runtime from the (glibc) node:22-bookworm-slim image — same
# Debian family as python:3.13-slim, so the binary runs as-is. Provides
# the `node` / `npm` / `npx` binaries used by stdio MCP subprocesses.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

# Pre-warm the npx cache so the first browser-use MCP spawn does not
# depend on registry reachability. `@latest` still re-resolves when a
# newer version is published and the container has network access.
RUN npx -y @playwright/mcp@latest --version || echo "npx prewarm skipped"

WORKDIR /app

COPY requirements.txt ./
# China-network default; override with --build-arg PIP_INDEX_URL=... if needed.
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# 官方框架基座：PyPI agentscope==2.0.10（与 agentscope-official 本地源码同版；
# 本地开发机用 editable 源码安装，见 README）
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} \
        "agentscope[service,storage-redis,storage-sql,channel,vdb-qdrant,rag]==2.0.10" \
        aiosqlite "pymupdf>=1.26" "pillow>=11.0" "websockets>=13" \
    && pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

# Framework patches (baked in at build time; supersedes the server-side
# thin deploy-patches/ overlay image, which is kept only as a hotfix path).
# Source of truth: server patches 20261015. When rebasing onto a newer
# agentscope version, re-apply the changes in deploy-patches/container/.
COPY deploy-patches/container/_redis_storage.py /usr/local/lib/python3.13/site-packages/agentscope/app/storage/_redis_storage.py
COPY deploy-patches/container/bge-small-zh-v1.5.yaml /usr/local/lib/python3.13/site-packages/agentscope/embedding/_openai/_models/bge-small-zh-v1.5.yaml

# Application assets (weights, workspaces, blobs, logs, .env stay out —
# see Dockerfile.dockerignore).
COPY main.py ./main.py
COPY server/ ./server/
COPY agents/ ./agents/
COPY skills/ ./skills/
COPY models/ ./models/
COPY scripts/ ./scripts/
COPY registry.json ./registry.json
COPY --from=frontend-build /build/web_ui/frontend/dist ./web_ui/frontend/dist

ENV PYTHONUTF8=1
# Informational only; the real port comes from AGENTS_BACKEND_PORT (.env).
EXPOSE 8000

CMD ["python", "main.py"]
