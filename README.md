# agentscop-platform

「官方架构 + 业务」的智能体平台。基座为官方 AgentScope **v2.0.10**
（本地源码 `../agentscope-main`，editable 安装，业务零侵入），
业务层由 `agentscope_extra-master`（骨架）与 `agent-new`（平台补丁）
两份工程合并而成。开发约束见 `AGENTS.md`，机制设计见 `docs/design.md`。

## ⚠️ 目录归属（先读这个，勿混淆）

| 目录 | 定位 | 注意 |
|---|---|---|
| `agentscop-platform`（本仓库） | **个人研究项目**：官方架构 + 业务的智能体平台，部署在个人阿里云服务器（新栈 6020） | 日常开发、镜像构建（`deploy/build-push.ps1`）都在这里 |
| `../agentscope_extra-master` | **公司开发项目**（同为活跃开发目录，本仓库业务骨架的来源） | 与本仓库**有意保持差异**：本仓库的部分改动未同步回公司项目，属有意保留 |
| `../agentscope-main` | AgentScope 框架源码（2.0.10dev），editable 安装 | 框架层改动在这里，且须同步 `deploy-patches/container/` 进镜像 |
| `d:\workspace\agentscop\.venv` | 两个项目**共用**的虚拟环境 | editable 指向 `agentscope-main\src`（2026-10-15 修正，原指向已不存在的 `agentscope-official`） |

**共用 venv 的后果**：两个项目的启动脚本都写死 `..\.venv\Scripts\python.exe`，
在哪个目录跑脚本，加载的都是同一个 venv + 同一份框架源码。因此：

- 不要用 `agentscope_extra-master` 的脚本启动——会以「公司业务代码 × 新框架源码」混跑，行为不可预期；
- 开发/启动一律在 `agentscop-platform` 目录下进行。

## 本地启动（零外部依赖）

```powershell
# 1) Python 依赖（复用工作区根 .venv 或自建）
& D:\workspace\agentscop\.venv\Scripts\python.exe -m pip install `
    -e "../agentscope-main[service,storage-redis,storage-sql,channel,vdb-qdrant,rag]" `
    -r requirements.txt

# 2) 前端依赖 + 启动（首次）
cd web_ui; pnpm install; cd ..

# 3) 一键启动（后端 :8020 + Vite :5173，浏览器开 http://localhost:8020）
.\start-dev.ps1          # 开发模式（Vite 热更新，FastAPI 单端口反代）
.\start-all.ps1          # 常驻模式（watchdog 守护）
.\stop-all.ps1           # 全部停止
```

> 本地后端端口用 **8020**：本机 `127.0.0.1:8000` 常被 CodeBuddy 等 IDE
> 的本地服务占用；容器/服务器部署仍是 8000（`AGENTS_BACKEND_PORT` 可覆盖）。

或者最小验证（不装前端依赖）：

```powershell
$env:AGENTS_BACKEND_PORT="8020"; python main.py    # API 服务；/docs 可看全部路由
```

首次启动自动建库（`data/agentscope.db`）、本地向量库（`data/qdrant_data/`）。
**账号**：`config/roles.json` 即用户表事实源（本仓库携带从服务器迁移的
真实用户表，口令为哈希，请用原账号登录）。全新环境：`.env` 设
`AGENTS_USERS=admin:你的密码` 首启自动生成用户表；两者皆无时认证关闭
（身份回退请求头），首次登录后到 `/team` 页建号改密。

### 默认模式 = 零依赖

| 组件 | 本地默认 | 生产/容器（`docker compose up`） |
|---|---|---|
| 存储 | SQLite `data/agentscope.db` | Redis（AOF） |
| 角色表 | `config/roles.json`（file 模式） | Redis `roles:*` |
| 向量库 | 进程内 Qdrant `data/qdrant_data/` | Qdrant 容器 |
| embedding | 不必需（仅 KB 索引用） | TEI 容器挂 `models/bge/` |

切换见 `.env.example`（`AGENTS_STORAGE` / `AGENTS_QDRANT_MODE` /
`AGENTS_ROLES_STORE`）。所有路径与开关集中在 `server/paths.py`。

## 可选组件

- **本地 embedding**（不想跑 TEI 容器时）：
  `pip install sentence-transformers` 后 `python embed_server.py`
  （:8003，OpenAI 兼容），凭据 base_url 填 `http://127.0.0.1:8003/v1`。
  BGE 权重已随仓库分发（`models/bge/`，Git LFS）。
- **老版 .doc 解析**：安装 LibreOffice 即自动启用（`server/doc_parser.py`）。
- **候选词动态占位符**：`config/dynamic_sources.json` 配置 SQL 数据源时
  `pip install psycopg2-binary`（未配置则占位符显示 —）。

## 目录速览

```
main.py               启动入口
server/app.py         装配层：create_app 全部扩展点 + 14 个业务接线
server/               业务扩展模块（认证/RBAC/模型/KB/会话/技能/解析器）
agents/               智能体声明（agent.json + system_prompt.md）
skills/               共享技能母版（播种到工作区）
server/internal_skills/  内部技能市场货架
config/               roles.json（用户表，gitignore）+ 候选词/动态源
models/               套餐模型卡片 + BGE 权重
web_ui/               vendored 前端（官方 2.0.10 + 业务微调）
scripts/              注册/同步/校验/冒烟/备份
deploy/               服务器部署物料（多阶段镜像 + 单端口 HTTPS compose）
data/  workspaces/    运行数据（gitignore）
```

## 智能体与技能

- 声明式智能体：`agents/<name>/agent.json` + `system_prompt.md`；
  `python scripts/register_agents.py` 注册（`registry.json` 为生成物）。
- 技能：`skills/<name>/SKILL.md`（UTF-8 无 BOM）；改后
  `python scripts/sync_skills.py`（移除加 `--prune`）传播到工作区。
- 内部技能市场：`server/internal_skills/` 下的技能在 UI `/hub` 可浏览安装。

## 脚本

| 命令 | 用途 |
|---|---|
| `python scripts/validate_setup.py` | 启动自检（认证/RBAC/KB/技能/工作区） |
| `python scripts/register_agents.py` | 注册智能体声明 |
| `python scripts/sync_skills.py` | 同步技能母版到工作区 |
| `python scripts/chat_smoke.py` | 对话冒烟测试 |
| `python scripts/kb_smoke.py` | 知识库冒烟测试 |
| `scripts/backup_apps.ps1` / `backup_apps.sh` | 应用+数据备份 |

## 服务器部署

`deploy/`：多阶段 Dockerfile（前端构建 + 运行时）、单端口 HTTPS compose、
`build-push.ps1`（ACR 推送）。详见 `deploy/DEPLOY-ALIYUN.md`。

## 与历史工程的关系

| 历史工程 | 在本工程中的去向 |
|---|---|
| `agentscope_extra-master` | 装配骨架（server/ 认证·RBAC·工作区·PDF·托管）、agents/ 声明、deploy/、start-*.ps1 |
| `agent-new` | 14 个平台补丁（`server/*_patch.py`、`remote_models` 等）、internal_skills/、embed_server.py、候选词配置 |
| `agentscope-official` | 基座（editable 依赖，不复制不入库） |

旧工程文件级映射与未迁移项（model groups 前端集成、
agentscope.db 旧库迁移）见 `docs/agents-mapping-from-agent-new.md` 与
`docs/design.md`。
