# 两套项目的关系与两套方案

> 目的：厘清 `d:\workspace\agentscop` 工作区里两套活跃工程的关系，分别梳理各自的技术方案，
> 并给出协同守则，避免混跑与误同步。
> 事实来源：各工程 `README.md` / `AGENTS.md` / `docs/design.md`、`deploy/` 物料、
> `../patches-20261015/README-WINDOWS.md`；核对日期 **2026-10-15**。

## 1. 一句话结论

- **`agentscope_extra-master` = 公司开发项目**（方案一）：AgentScope **PyPI 2.0.8** 之上的
  自包含定制工程，重基础设施（Redis + Qdrant + TEI 容器），部署在公司侧。
- **`agentscop-platform` = 个人研究项目**（方案二）：官方架构 **v2.0.10** + 业务合并工程，
  由 `agentscope_extra-master`（骨架）与 `agent-new`（14 个平台补丁）合并而来，
  本地可零依赖运行，部署在个人阿里云服务器。
- **`agentscope-main` = 两套方案共用的框架基座**：官方源码（2.0.10dev），
  editable 安装进共用 `.venv`，业务对其零改动（框架级定制集中在 `deploy-patches/container/`）。
- 两套业务工程**有意保持差异**：`agentscop-platform` 的部分改动未同步回公司项目，属有意保留。

## 2. 工作区全景

```
d:\workspace\agentscop\
├── .venv\                        ← 两套工程共用的虚拟环境（editable 指向 agentscope-main\src）
├── agentscope-main\              ← 框架基座：官方 AgentScope 源码（2.0.10dev），editable 安装
├── agentscope_extra-master\      ← 【方案一】公司开发项目（活跃开发中）
├── agentscop-platform\           ← 【方案二】个人平台（日常开发、镜像构建都在这里）
├── patches-20261015\             ← 阿里云服务器框架补丁包（2 个 commit 的 patch）
├── agentscope-server-patches-20261015.tar.gz
└── pre-rewrite-backup-20260930.bundle   ← 重写前备份
```

| 目录 | 定位 | 备注 |
|---|---|---|
| `agentscop-platform` | 个人研究项目，部署在个人阿里云服务器 | 日常开发入口 |
| `agentscope_extra-master` | 公司开发项目；本仓库业务骨架的来源 | 与 platform 有意保持差异 |
| `agentscope-main` | 框架源码（editable），业务零侵入 | 框架改动须同步 `deploy-patches/container/` 进镜像 |
| `.venv` | 两工程共用 | 启动脚本均写死 `..\.venv\Scripts\python.exe` |

## 3. 血缘关系

```
        AgentScope 官方（PyPI / GitHub）
                 │
       ┌─────────┴──────────────────────────┐
       │ agentscope-main（本地源码 2.0.10dev）│  ← 共用基座，editable 安装
       └─────────┬──────────────────────────┘
                 │（业务零改动；只走 create_app 扩展点）
    ┌────────────┴─────────────────────────────────────┐
    │                                                  │
 agentscope_extra-master（公司项目）              agentscop-platform（个人平台）
 基座 = PyPI agentscope==2.0.8                   基座 = 本地源码 editable v2.0.10
 自包含定制工程（骨架）                ←──合并──  = extra 骨架 + agent-new 14 补丁
                                                  （部分改动有意不同步回公司项目）
```

关键事实：

1. **合并来源**：`agentscop-platform` 的业务层由两份工程合并——
   `agentscope_extra-master`（装配骨架：认证 / RBAC / 按用户工作区 / PDF 视觉解析 /
   单端口托管 / 部署物料）+ `agent-new`（14 个平台补丁：动态模型发现 / 技能市场 /
   会话与 KB 增强 / 候选词 / 僵死自愈等）。文件级映射见
   [`agents-mapping-from-agent-new.md`](agents-mapping-from-agent-new.md)。
2. **有意差异**：README 明确声明 platform 的部分改动**未同步回**公司项目，属有意保留
   （详见 §7）。
3. **共用 venv 的后果**：两个项目的启动脚本都写死 `..\.venv\Scripts\python.exe`，
   在哪个目录跑脚本，加载的都是同一个 venv + 同一份框架源码。因此：
   - 不要用 `agentscope_extra-master` 的脚本启动——会以「公司业务代码 × 新框架源码」混跑，
     行为不可预期；
   - 开发/启动一律在 `agentscop-platform` 目录下进行。
4. **框架级定制**：业务对 `agentscope-main` 零改动；唯二的框架补丁
   （`_redis_storage.py` 智能体列表排序 +1 行、`bge-small-zh-v1.5.yaml` 模型卡）
   以「薄自建镜像」（官方镜像 + COPY 补丁文件）方式进入部署，物料在
   `agentscop-platform/deploy-patches/container/`，补丁包见 `../patches-20261015/`。

## 4. 方案一：agentscope_extra-master（公司项目）

**定位**：AgentScope 2.0 之上的**独立完整工程**——后端装配层、智能体声明、skill 固化母版、
vendored Web UI、启动脚本与运行数据全部收敛于此；全程原生机制，本体与克隆仓库零改动。

| 维度 | 内容 |
|---|---|
| 框架基座 | **PyPI `agentscope==2.0.8`**（site-packages，非 editable）；参考克隆与运行同版，「看码即看运行」 |
| 装配入口 | `server/main.py`（`create_app` 装配） |
| server 模块 | 11 个：`main` `model_policy` `per_user_workspace` `auth` `roles_store` `roles_admin_api` `role_access_policy` `share_policy`（遗留）`pdf_figures_parser` `figures_router` 等 |
| 基础设施 | **重依赖容器栈**：Redis(6379) + Qdrant(6333) + TEI(19556)，全部由 `start-all.ps1` 幂等拉起 |
| 存储 | Redis（AOF）；用户表亦已迁 Redis（roles.json 降级为首启种子 + 备份，2026-10-12 Phase 7） |
| 认证/RBAC | HTTP Basic + 「归属即规则」RBAC；`config/roles.json` + `/roles` 管理 API；口令 PBKDF2 哈希 |
| 知识库 | Qdrant 容器 + TEI 托管 `BAAI/bge-small-zh-v1.5`（512 维）；PDF 走 `PdfWithFiguresParser` 视觉转写 |
| 共享授权 | 早期 `.env` 模式（`AGENTS_OWNER` / `AGENTS_DEFAULT_SHARE` / `AGENTS_AGENT_SHARE`，`share_policy.py` 已遗留） |
| 端口 | 后端 8000；vendored web_ui（Express:3000 + Vite:5173） |
| 特有能力 | `scripts/checks/` 模型连通性验证；`configure_model.py` / `configure_embedding.py` 管理员一键脚本；`.opencode/`（`/deploy-release` 命令与 agentscope-deploy 运维技能） |
| 部署 | `deploy/`（多阶段 Dockerfile + compose + `build-push.ps1`），镜像经**电信云 Nexus**（150.223.194.46:31382，`agentscope/` 前缀）分发，部署在公司侧 |

**已知边界**：本地工作区无文件围栏（多租户文件隔离需切容器沙箱，design.md §10）；
重跑注册脚本覆盖 UI 手改；web_ui vendor 后与官方前端升级脱钩；
知识库跨用户共享为后续路线。详见其 `docs/design.md`。

## 5. 方案二：agentscop-platform（个人平台）

**定位**：「官方架构 + 业务」的**合并工程**。基座为官方 AgentScope **v2.0.10**（本地源码
editable 安装，业务零侵入），业务层由 extra 骨架 + agent-new 补丁合并而成；
**本地默认零外部依赖**即可起全栈，部署在个人阿里云服务器。

| 维度 | 内容 |
|---|---|
| 框架基座 | **editable 本地源码 `../agentscope-main`（v2.0.10dev）**；镜像内从 PyPI 装同版 2.0.10（venv 曾误指向已不存在的 `agentscope-official`，2026-10-15 修正） |
| 装配入口 | `main.py` → `server/app.py`：`create_app` 全部扩展点 + **14 个业务接线**（所有业务改动的汇聚点），各补丁独立成模块、互不依赖 |
| server 模块 | 26 个 + `internal_skills/`：在 extra 骨架（auth/RBAC/工作区/PDF）之上增加 `remote_models` `model_groups_api` `embedding_models_patch` `kb_reindex_api` `kb_credential_fallback_patch` `skill_visibility` `internal_skill_hub` `github_skill_hub` `session_permission_patch` `session_replay_patch` `tool_offload_patch` `stuck_run_guard` `quick_prompts_api` `doc_parser` `paths.py` `zhipu_credential` 等 |
| 存储 | **双轨**：本地默认 `AGENTS_STORAGE=sqlite`（`data/agentscope.db`，零外部依赖）；生产 `redis`（AOF）。凡直读 SQLite 的补丁都带 redis 守卫/降级 |
| 向量库 | 本地 = 进程内 Qdrant（`data/qdrant_data/`）；生产 = Qdrant 容器。开关 `AGENTS_QDRANT_MODE` |
| embedding | 可选：TEI 容器挂 `models/bge/`（LFS），或 `embed_server.py`（:8003，OpenAI 兼容，sentence-transformers） |
| 认证/RBAC | `config/roles.json` 即用户表事实源（file 模式）/ Redis `roles:*`（生产）；管理页 `/team` 热生效；全无配置时认证关闭（身份回退请求头） |
| 技能体系 | 共享母版 `skills/` + 内部技能市场 `server/internal_skills/`（UI `/hub` 浏览安装）+ GitHub 技能市场；按角色可见（`skill_visibility.py`） |
| 会话增强 | 权限补丁 / 重放上限 / 工具卸载 / 僵死自愈（`stuck_run_guard`） |
| 模型 | 智谱 Coding Plan 凭据 + 本地 BGE 凭据（`zhipu_credential.py`）；远端模型动态发现 + `/model_groups` 分组；`models/*.yaml` 套餐卡片 |
| 候选词 | `quick_prompts_api` + 动态占位符（`config/dynamic_sources.json`，可选 SQL 数据源） |
| 端口 | 本地后端 **8020**（`127.0.0.1:8000` 常被 IDE 占用；容器/服务器仍 8000，`AGENTS_BACKEND_PORT` 可覆盖）；dev 单入口：FastAPI 反代 Vite 5173（含 HMR） |
| 部署 | 个人阿里云服务器：`deploy/` 多阶段 Dockerfile + 单端口 HTTPS compose + `build-push.ps1`（Nexus）；另有**薄自建镜像** `agentscop-platform:custom-20261015`（官方镜像 + 2 个框架补丁文件，物料 `deploy-patches/container/`，服务器分支 `server-patches`，基于官方 v2.0.10 e6c014f0） |

**约束要点**（摘自 `AGENTS.md`）：路径一律取自 `server/paths.py`；SKILL.md 必须 UTF-8 无 BOM；
skill/agent 声明改后必须跑 `scripts/sync_skills.py` / `register_agents.py`；
`.ps1` 新增注释一律 ASCII；模型大文件走 Git LFS。

## 6. 两套方案对比矩阵

| 维度 | 方案一 `agentscope_extra-master` | 方案二 `agentscop-platform` |
|---|---|---|
| 定位 | 公司开发项目 | 个人研究项目 |
| 框架版本/安装 | PyPI **2.0.8**（site-packages） | 本地源码 editable **2.0.10**；镜像内 PyPI 同版 |
| 装配入口 | `server/main.py` | `main.py` → `server/app.py` |
| server 模块规模 | 11 个 | 26 个 + `internal_skills/` |
| 本地启动依赖 | 需 Redis + Qdrant + TEI 容器 | **零依赖**（SQLite + 进程内 Qdrant） |
| 生产存储 | Redis | Redis（本地可切 SQLite，双轨） |
| 用户表 | Redis（roles.json 为种子） | roles.json（file）/ Redis 双模式 |
| 技能市场 | 无（共享母版 + agent 绑定） | 内部技能市场 `/hub` + GitHub 技能市场 + 按角色可见 |
| 模型目录 | `models/*.yaml` 静态卡片 | 卡片 + **远端动态发现** + 模型分组 |
| 会话治理 | 基础 | 权限/重放上限/工具卸载/僵死自愈 |
| 文档解析 | PDF 视觉解析（PdfWithFiguresParser） | 同左 + `.doc`（LibreOffice）+ 候选词动态源 |
| 后端端口 | 8000 | 本地 8020 / 容器 8000 |
| 前端 | vendored（Express 3000 + Vite 5173） | vendored（官方 2.0.10 + 业务微调 + LAN polyfill） |
| 部署目标 | 公司侧（Nexus） | 个人阿里云（新栈 6020）+ 薄自建镜像 |
| 特有目录 | `.opencode/`、`scripts/checks/` | `internal_skills/`、`deploy-patches/container/`、`embed_server.py` |

## 7. 有意差异清单（勿「修复」）

以下差异是 platform **有意保留**、未同步回公司项目的（README「目录归属」声明）：

- `server/app.py` 装配层 + 14 个 agent-new 平台补丁模块（动态模型发现、技能市场、
  会话与 KB 增强、候选词、僵死自愈等）；
- 存储双轨能力（SQLite 零依赖模式 + 各补丁的 redis 守卫/降级）；
- `internal_skills/` 内部技能市场、`github_skill_hub`、`skill_visibility`；
- 候选词动态占位符、`.doc` 解析、`embed_server.py`；
- 本地端口策略（8020）与 `.venv` editable 指向（`agentscope-main`）。

反向地，公司项目特有、platform 未收录的：`.opencode/` 运维技能、`scripts/checks/` 连通性验证等。
**同步需求出现时按清单选择性 cherry-pick，不做全量对齐。**

## 8. 协同守则

1. **开发/启动一律在 `agentscop-platform` 目录下进行**；禁止用 `agentscope_extra-master`
   的启动脚本（共用 venv 会以「公司业务 × 新框架」混跑，行为不可预期）。
2. **框架层改动**：改 `agentscope-main` 后，须同步 `deploy-patches/container/` 下的
   补丁文件进镜像；业务仓库本身保持零侵入。
3. **同步方向**：默认单向（platform 内消化改进）；如需回馈公司项目，按 §7 清单
   选择性移植，并注意两侧框架版本不同（2.0.10 vs 2.0.8，`create_app` 扩展点签名需复核）。
4. **升级框架时**：改 requirements/editable 指向 → 重装 → 回归
   `scripts/validate_setup.py` + `chat_smoke.py` 全绿再投入使用（extra 侧另需同步参考克隆）。
5. 镜像升级流程与服务器现状见 `../patches-20261015/README-WINDOWS.md`
   （当前：`agentscop-platform:custom-20261015`，分支 `server-patches`，Redis 卷持久化）。

## 9. 相关文档索引

| 文档 | 内容 |
|---|---|
| `../README.md`（platform） | 目录归属表、启动方式、与历史工程的关系 |
| `../AGENTS.md` | 开发约束、server 模块索引 |
| [`design.md`](design.md) | 机制设计与历史决策（含 §13 多租户加固、§14 知识库落地） |
| [`guide.md`](guide.md) | 给同事的核心概念速览 |
| [`agents-mapping-from-agent-new.md`](agents-mapping-from-agent-new.md) | agent-new 文件级映射与未迁移项 |
| `../../agentscope_extra-master/README.md`、`docs/design.md` | 方案一的权威文档 |
| `../../patches-20261015/README-WINDOWS.md` | 服务器框架补丁包与镜像维护流程 |
| `../deploy/DEPLOY-ALIYUN.md` | 部署手册 |
