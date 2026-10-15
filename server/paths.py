# -*- coding: utf-8 -*-
"""集中管理项目路径与部署模式开关。

所有需要落盘路径的业务模块（补丁、KB、角色表）统一从这里取，
避免各模块各自推导相对路径导致换目录启动丢数据。

环境变量（均有默认值，本地零配置可启动）：

============================  ==========================================  ==========
变量                          含义                                        默认值
============================  ==========================================  ==========
AGENTS_STORAGE                存储后端：sqlite / redis                    sqlite
AGENTS_QDRANT_MODE            向量库模式：path（本地文件）/ url（服务端）  url 配置了
                              （path 模式无需 Qdrant 容器）               AGENTS_QDRANT_URL
                                                                          则 url，否则 path
AGENTS_DB_PATH                SQLite 数据库文件路径                       data/agentscope.db
AGENTS_BACKEND_PORT           后端监听端口                                8000
============================  ==========================================  ==========

data/ 目录（数据库、blobs、本地 Qdrant）属于运行时数据，
已列入 .gitignore，不随仓库分发。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 运行时数据目录（gitignore）────────────────────────────────────
DATA_DIR = os.path.join(ROOT, "data")
BLOBS_DIR = os.path.join(DATA_DIR, "blobs")  # 知识库上传文档
QDRANT_DIR = os.path.join(DATA_DIR, "qdrant_data")  # 本地向量库（path 模式）

# ── 持久化业务数据 ────────────────────────────────────────────────
WORKSPACES_DIR = os.path.join(ROOT, "workspaces")  # 工作区（可整目录备份）
CONFIG_DIR = os.path.join(ROOT, "config")
SKILLS_DIR = os.path.join(ROOT, "skills")
MODELS_DIR = os.path.join(ROOT, "models")
DB_PATH = os.path.abspath(
    os.environ.get("AGENTS_DB_PATH") or os.path.join(DATA_DIR, "agentscope.db")
)
SQLITE_URL = "sqlite+aiosqlite:///" + DB_PATH

# ── 部署模式开关 ─────────────────────────────────────────────────
# 存储后端：sqlite（单机零依赖）或 redis（生产/容器部署）
STORAGE_MODE = (os.environ.get("AGENTS_STORAGE") or "sqlite").strip().lower()

# 向量库模式：path = 进程内本地文件库（零依赖）；url = Qdrant 服务
QDRANT_MODE = (
    os.environ.get("AGENTS_QDRANT_MODE")
    or ("url" if os.environ.get("AGENTS_QDRANT_URL") else "path")
).strip().lower()

# 角色表存储模式跟随存储后端（RedisStorage 才支持 redis 模式的
# roles:* 键族）；显式设置 AGENTS_ROLES_STORE 可覆盖。
os.environ.setdefault(
    "AGENTS_ROLES_STORE",
    "redis" if STORAGE_MODE == "redis" else "file",
)

# 确保落盘目录存在（SQLite 无法在不存在的目录里建库文件；
# Qdrant path 模式同理）。幂等。
for _dir in (DATA_DIR, BLOBS_DIR, QDRANT_DIR, WORKSPACES_DIR, CONFIG_DIR):
    os.makedirs(_dir, exist_ok=True)
