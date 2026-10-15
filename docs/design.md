# agentscope_extra 设计文档

> 📖 给同事的简明版：核心概念与机制速览见 [`docs/guide.md`](guide.md)。
> 状态：v2.3（2026-09-29）——知识库落地（Qdrant 持久化 + 本地 BGE embedding + 全链路验证，§14）；此前 v2.2 为多租户加固四阶段（§13）。本文档已与当前实现对齐核对
> 结论先行：**定制层是完全独立的 git 工程，agentscope 本体/克隆与本地运行已彻底解耦——运行时加载 PyPI 安装包，克隆仅作参考；所有定制走原生扩展点，对本体零改动。**

## 1. 背景与目标

- 本工程（`agentscope_extra`）构建于 AgentScope 2.0.8 之上，是**自包含的定制工程**：后端装配层、智能体声明、skill 母版、vendored Web UI、启动脚本、运行数据全部收敛于此。
- 目标：
  1. 智能体以配置文件形式管理（版本化、可 diff、脚本幂等注册）；
  2. 固定脚本/报表模板等资产固化为 skill，平台共享 + 按 agent 差异化；
  3. 模型由管理员全局配置，用户无感，且限定为智谱 Coding Plan 套餐内模型；
  4. 多租户：用户间输出隔离、基于角色的资源可见性（RBAC「归属即规则」+ `user_agents` 白名单）、HTTP Basic 认证（口令哈希存 `config/roles.json`）；
  5. 全程原生机制，本体与克隆仓库零改动。

## 2. 定制层架构

### 2.1 工作区分层

```
agentscope_workspace/
├── .venv/                  ← 运行时依赖：PyPI 安装 agentscope==2.0.8（site-packages）
├── .vscode/launch.json     ← 工作区级调试配置（指向 agentscope_extra，不入仓库）
├── agentscope/             ← 本体参考代码（浅克隆 v2.0.8，零本地改动，与运行无关）
└── agentscope_extra/       ← ★ 定制层（独立 git 仓库 → codeup）
   ├── server/              ← 装配层：main.py（create_app 装配）+ model_policy.py
   │                          （模型目录）+ per_user_workspace.py（用户级工作区）
   │                          + auth.py（Basic 认证，口令哈希读 config/roles.json）
   │                          + role_access_policy.py（RBAC）+ roles_admin_api.py（/roles）
   ├── models/              ← 模型卡片（套餐内目录的唯一事实源）
   ├── web_ui/              ← vendored 官方前端（backend/Express:3000 + frontend/Vite:5173）
   ├── agents/              ← 智能体声明（配置层）
   ├── skills/              ← skill 母版（资产层，平台共享）
   ├── workspaces/ blobs/ logs/ .env             ← 数据层（gitignore；workspaces 内为
   │                                              <agent_id>/users/<user_id>/ 两级布局）
   ├── scripts/ (+checks/)  ← 运维脚本层（注册/投放/验证/冒烟/管理员配置/认证探针）
   ├── start-all/dev/stop-all.ps1                ← 服务入口
   └── requirements.txt registry.json docs/
```

### 2.2 运行链路

```
本地 dev（start-all / start-dev 注入 AGENTS_DEV_FRONTEND_URL，固定 dev 模式，与 dist 无关）：
浏览器 → FastAPI(8000, server/main.py 装配; import 的是 .venv 里的 PyPI agentscope)
       ├─ 非 API 路径 → 反向代理 → Vite dev(5173, web_ui/frontend)，HTTP 转发 + HMR WebSocket 转发
       └─ Redis(6379, Docker, agentscope-redis) / Qdrant(6333) / TEI(19556)

部署（容器/服务器，无该环境变量）：FastAPI 挂载 web_ui/frontend/dist 单端口 SPA（8000 / 8005 HTTPS）
```

> dev 代理在 `main.py` 末尾按环境变量切换：设了 `AGENTS_DEV_FRONTEND_URL` 就只走
> Vite 代理（dist 完全不参与），没设才挂载 dist——两端行为互不干扰。

### 2.3 职责划分

| 层 | 内容 | 修改方式 |
|---|---|---|
| 装配层 | `server/main.py`：存储/总线/workspace manager/知识库/hub/子代理模板/渠道/CORS/extra_credentials/resource_access_policy；`per_user_workspace.py`（用户级工作区）；`auth.py`（Basic 认证）；`role_access_policy.py`（RBAC 策略）+ `roles_admin_api.py`（/roles 管理 API）；`model_policy.py`（模型目录） | 直接编辑 + 重启 |
| 资产层 | `agents/*`（agent.json + system_prompt.md + 差异化 skills/）、`skills/*`（共享母版）、`models/*.yaml` | 编辑 + 重跑对应脚本 |
| 运维层 | `scripts/*`：register_agents（幂等 upsert）/ sync_skills（播种同步）/ validate_setup（可见性矩阵+隔离断言）/ chat_smoke / configure_model（管理员）/ _auth_probe（认证矩阵） | 手动执行 |
| 数据层 | `workspaces/<agent_id>/users/<user_id>/`（记忆+技能分区+会话产物）、`blobs/`、`logs/`、`.env`、`config/roles.json`（用户/角色/白名单/口令哈希，管理页维护） | 框架自动管理，不入库 |

## 3. 与本体代码的隔离

### 3.1 隔离机制（已逐项验证）

| 维度 | 机制 | 验证事实（2026-09-24） |
|---|---|---|
| 运行隔离 | 运行时加载 `.venv/Lib/site-packages` 的 PyPI 包（非 editable） | `inspect.getfile(agentscope)` → site-packages，不在克隆内 |
| 代码隔离 | agentscope 克隆零本地改动 | `git -C agentscope status --porcelain` 为空 |
| 引用隔离 | 本工程代码/脚本对克隆路径零引用 | 全量扫描 `*.py/*.ps1/*.json` 无 `..\agentscope` 引用 |
| 数据隔离 | 运行数据全在本工程（workspaces/blobs/logs/.env） | `examples/agent_service/` 仅剩官方原版文件 |
| 定制隔离 | 定制全走原生扩展点：`create_app` 装配参数、`extra_credentials`、REST API、skill 分区文件投放（框架 reconcile 机制） | 无任何 `src/agentscope` 补丁 |

**后果**：克隆可随时删除、重克隆或切换版本，本地运行不受任何影响；升级框架也不会遇到本地补丁冲突。

> 注：本节讲的是"定制层 vs 本体"的隔离。多租户场景下用户之间的隔离（`users/<user>`
> workdir、Basic 认证、共享授权策略）见 §13 与 §9——共享 agent 模式下
> `users/<user>` 段是用户间唯一的文件隔离边界。

### 3.2 升级本体时的同步约定（重要）

运行版本由 `requirements.txt` 锁定（`agentscope==2.0.8`），参考克隆当前同为
v2.0.8——因此"看克隆源码 == 看运行行为"。**升级 agentscope 包时必须同步参考代码**：

1. 改 `requirements.txt` 版本号 → `pip install -r requirements.txt` 重装 .venv；
2. 同步参考克隆到对应版本（`git -C agentscope fetch --tags && git -C agentscope checkout v2.0.x`，或按新版本重克隆），保证"看码即看运行"；
3. `web_ui/` 是 vendor 副本（冻结在 v2.0.8，来源见提交③信息），官方前端更新时需**手动同步**（对照 diff，node_modules/dist 不入库）；
4. 本文档引用的源码行号均基于 v2.0.8，升级后需复核（尤其 §5 表格与 §8.1 的两处路由行为）；
5. 复核装配层扩展点在新版的可用性：`create_app(extra_credentials=...)`、
   `ChatModelBase.list_models(custom_yaml_dir)`、`/model` 路由经
   `get_chat_model_class().list_models()` 取目录、workspace 分区布局与 reconcile 语义；
6. 升级后回归：`scripts/validate_setup.py` + `scripts/chat_smoke.py` 全绿再投入使用。

## 4. 目录结构

```
agentscope_extra/
├── README.md                      # 使用说明
├── docs/design.md                 # 本文档
├── server/
│   ├── __init__.py
│   ├── main.py                    # 装配入口（提交②为官方原版，④起为改造版）
│   ├── model_policy.py            # ZhipuCodingPlanCredential + ZhipuPlanChatModel
│   ├── per_user_workspace.py      # PerUserLocalWorkspaceManager（users/<user> workdir）
│   ├── auth.py                    # HTTP Basic 认证（dependency_overrides；口令哈希读 config/roles.json）
│   ├── role_access_policy.py      # RoleBasedAccessPolicy（归属即规则 RBAC）
│   ├── roles_admin_api.py         # GET/POST /roles（仅 admin；保存时明文自动哈希化）
│   └── share_policy.py            # （遗留，不再被引用）OwnedAgentsSharePolicy——保留备回滚
├── models/glm-5.3-flash.yaml      # 套餐内模型卡片
├── agents/                        # 智能体声明
│   ├── personal_assistant/        # 个人助手（默认对所有人共享）
│   ├── report_agent/              # 周报助手（含差异化 skills/report_style）
│   └── qa_agent/                  # 数据质检员
│   （每个目录 = agent.json + system_prompt.md + 可选 skills/）
├── skills/                        # 平台共享 skill（当前仅 sync_probe 分发探针；
│                                   #   周报助手/数据质检员的技能已改为 agent 绑定，
│                                   #   位于 agents/<name>/skills/）
├── web_ui/                        # vendored 官方前端（不含 node_modules/dist）
├── scripts/
│   ├── common.py                  # base_url / 认证头 / AGENTS_USERS·AGENTS_SHARE 解析 / HTTP 封装
│   ├── register_agents.py         # agent upsert（幂等，admin 单身份）
│   ├── sync_skills.py             # 播种同步：覆写 .seed + 增量投放（支持 --prune/--agent）
│   ├── validate_setup.py          # 可见性矩阵 + workdir 隔离 + 技能断言
│   ├── configure_model.py         # 管理员：凭据收敛 + 存量会话模型修复（遍历 AGENTS_USERS）
│   ├── chat_smoke.py              # LLM 全链路冒烟（按名解析可见 agent）
│   ├── _auth_probe.py             # 认证矩阵探针（401/401/200）
│   └── checks/                    # 模型连通性验证（原工作区根 scripts 迁入）
├── workspaces/<agent_id>/users/<user_id>/   # 运行数据（不入库）
├── start-all.ps1 / start-dev.ps1 / stop-all.ps1
├── requirements.txt
└── registry.json                  # 生成物：name → {agent_id}
```

## 5. 依托的原生机制（源码行号基于 v2.0.8，升级需复核）

| 机制 | 出处 | 本工程用法 |
|---|---|---|
| 智能体=配置记录 | `app/_router/_schema/_agent.py:12` `CreateAgentRequest` | `agents/*/agent.json` → REST 注册 |
| Agent upsert API | `POST /agent/`、`PATCH /agent/{id}`、`GET /agent/` | 注册脚本按 name 幂等 upsert |
| Skill 格式 | `skill/_local_loader.py:42-77`：目录 + SKILL.md（frontmatter name/description 必填） | 一 skill 一目录 |
| Skill 分区与账实核对 | `workspace/_local_workspace.py:474-539`：mtime 对比 `.index`，自动收编手工增删 | 投放方案的依据 |
| 分区装备 + seed 播种 | `_equip_partition`（`workspace/_base.py:413-449`）+ `skill_paths` 播种（`_local_workspace.py:164-186`） | 共享技能经 `skill_paths` 播种（Phase 2 起）；差异化技能用投放 |
| initialize 幂等 | `_local_workspace.py:164-186` | 脚本可安全预建目录 |
| 自定义凭据扩展点 | `create_app(extra_credentials=[...])` + `CredentialFactory` | 注册 `zhipu_coding_plan` 类型 |
| 模型目录自定义 | `ChatModelBase.list_models(custom_yaml_dir)`（`model/_base.py:135`） | 套餐内模型卡片目录 |
| 跨用户资源共享策略 | `create_app(resource_access_policy=...)` + `ResourceAccessPolicyBase`（`access/_policy.py:79`），默认 `DenyAllResourceAccessPolicy`（`:153`） | `RoleBasedAccessPolicy`（`server/role_access_policy.py`）：归属即规则——admin 名下公共（KB/凭证全员只读、agent 按 `user_agents` 白名单），用户自建私有 |
| 身份依赖替换 | FastAPI `app.dependency_overrides`（框架身份入口 `app/deps.py:33` `get_current_user_id`） | `server/auth.py` 全站替换为 Basic 校验 |

**明确不使用**：已废弃的 `POST /workspace/skill` 路径安装（`deprecated=True`）、软链接（分区可写会穿透污染母版）。
（v2.0 时期"不使用 skill_paths 播种"的约束已随 Phase 2 解除——装配层迁入本工程后，该参数只是本工程自己的装配项，不构成对本体改动。）

## 6. 智能体注册流程（register_agents.py）

1. 扫描 `agents/*/agent.json`；约定键 `system_prompt_file` 指向同目录提示词 md，脚本读入填充 `system_prompt` 后移除该键（不发给服务端）。
2. `GET /agent/` 按 `data.name` 查重：命中 → `PATCH /agent/{id}`；未命中 → `POST /agent/`。
3. 结果写入 `registry.json`。
- 幂等策略：upsert（文件是唯一事实源，重跑会覆盖 UI 手工修改）。
- 手动环节：`python scripts/register_agents.py`。

## 7. Skill 投放与播种（S2 混合形态）

- **共享技能（自动播种）**：装配层把 `skills/*` 母版传入 manager 的
  `skill_paths` → 新工作区创建时自动写入 `skills/.seed`，用户首次触达自动装备。
  新用户**零操作**即获得全部共享技能。此路径经 manager 内部打包上传，与后端类型无关。
- **更新传播（sync_skills.py）**：框架的 seed/装备都"存在即跳过"，母版更新必须重跑脚本——
  ① 覆写每个 workspace 的 `skills/.seed`（未来装备用新版）
  ② 增量覆盖已装备分区 `skills/<agent_id>/`（共享+差异化，reconcile 自动收编）
  ③ `--prune` 清理母版已删除的目录。
- **差异化技能（本地投放）**：`agents/<name>/skills/*` 投放到对应 (agent, user) 分区；
  投放对象清单 = `.env` `AGENTS_USERS` 的全部键 ∪ `workspaces/<agent_id>/users/*`
  已存在目录（覆盖共享访问者）；新用户的差异化技能需重跑 sync（README 已注明）。
- **投放目标（per-user 布局）**：
  ```
  agentscope_extra/workspaces/<agent_id>/users/<user_id>/skills/<agent_id>/
  ```
- 校验：同一 agent 的共享∪差异化 frontmatter `name` 全局唯一（同分区重名互相顶替）。
- 手动环节：`python scripts/sync_skills.py [--prune] [--agent 名字]`。

## 8. 模型全局收敛（configure_model.py + model_policy.py）

### 8.1 问题与机制

- 框架无"全局默认模型"设置；UI 会把**候选列表第一个**自动写入无模型会话
  （`ChatViewport.tsx:590-613`），候选列表来自 `/model` 路由 →
  `credential.get_chat_model_class().list_models()`——包内置 OpenAI 卡片目录，写死。
- 解法（原生扩展点，本体零改动）：
  1. `server/model_policy.py` 定义 `ZhipuPlanChatModel(OpenAIChatModel)` 覆写
     `list_models()` 只读 `models/*.yaml`，以及
     `ZhipuCodingPlanCredential(OpenAICredential)`（type=`zhipu_coding_plan`）
     的 `get_chat_model_class()` 绑定该模型类——**注意必须绑定在 ChatModel 类上，
     仅覆写凭据的 `list_models` 无效**（路由绕过它）；
  2. `server/main.py` 装配 `extra_credentials=[ZhipuCodingPlanCredential]`；
  3. `scripts/configure_model.py`：确保凭据 → 删除旧 `openai_credential` →
     修复存量会话 `chat_model_config`（自动遍历 `.env` `AGENTS_USERS` 的全部用户；对某用户不可见的 agent 会 404，脚本自动跳过）。
- 效果：所有模型选择器（聊天/定时/渠道）只剩套餐内模型，新会话 UI 自动默认；
  限制为 UI 级（绕过 UI 的 API 调用由智谱服务端拒绝兜底）。
- 套餐内新增模型 = 在 `models/` 加一个 yaml 卡片 + 重启。
- 运行时构造路径不变：`get_model` 经 `CredentialFactory.from_dict` →
  `get_chat_model_class()` → 子类继承 `OpenAIChatModel`（chat_smoke 已验证）。

### 8.2 实施中发现的坑（已记入提交 fe0c7e1）

- `python server\main.py` 时 `sys.path[0]` 是脚本目录，uvicorn 导入串
  `server.main:app` 解析不到 → 入口处将工程根插入 `sys.path`。
- 类体内定义了字段 `type`（str）时，方法注解写内建 `type[...]` 会被遮蔽
  （`"str"[...]` TypeError）→ 注解用 `typing.Type`。
- `/model` 路由取目录的实际路径是 `get_chat_model_class().list_models()`
  （`_router/_model.py:39`），绕过凭据层覆写。

## 9. 边界与已知限制

1. **本地工作区无文件围栏（安全边界声明）**：`LocalWorkspace` 后端按"调用者可信"设计——Read/Write/Bash 可达宿主任意路径，包括 `workspaces/<agent_id>/users/` 下**其他用户的目录**（已实测：u2 会话借 agent 工具复制 admin 文件，2026-09-28）。权限系统不构成替代：`working_directories` 只是 ACCEPT_EDITS/DONT_ASK 模式的自动放行范围（`permission/_context.py:9-22`），deny 规则可被 Bash 命令变体绕过。**多租户的文件隔离必须使用沙箱型后端**（Docker/E2B/K8s/Bubblewrap——容器文件系统即边界，仅挂载该用户自己的 workdir），见 §10。
2. **差异化 skill 投放仍本地耦合**：共享技能已走 `skill_paths` 播种（与后端类型无关），但按 agent 差异化的投放仍是本地文件拷贝——换 Docker/E2B 时改走 `POST /workspace/skill/upload`（演进路线）。
3. **web_ui 与官方升级脱钩**：vendor 冻结在 v2.0.8，官方前端更新需手动同步（§3.2）。
4. **参考代码同步义务**：升级 agentscope 包必须同步克隆与本文档行号（§3.2）。
5. ~~口令明文存储~~ **已解决（2026-10-08）**：口令改存 `config/roles.json` 的 PBKDF2-SHA256 哈希（`sha256$<salt>$<hash>`，gitignore）；`.env` 仅作首启引导兜底。
6. **共享为整 agent 粒度 READ**：授权粒度是"agent 对用户"，无会话级/工具级授权；被授权者不能编辑 agent（owner 专属）。
7. **文件源覆盖 UI**：重跑注册脚本覆盖 UI 手工提示词修改。

（v2.0 的"布局耦合/输出隔离/身份无认证"三项已在 §13 多租户加固中解决或收窄；"跨用户共享 agent"已在 §13 Phase 4 落地。）

## 10. 演进路线（未排期）

- **多租户沙箱化（安全修复，优先）**：把 `per_user_workspace.py` 的 PerUser 语义迁移到 `DockerWorkspaceManager` 子类——每 (agent, user) 一个容器，仅挂载 `workspaces/<agent_id>/users/<user_id>/`，容器文件系统即边界；Windows 本地依赖 Docker Desktop（Redis 容器已有运行基础），框架自带镜像预热（prewarm）缓解启动延迟。
- **差异化 skill 后端无关化**：改走 `POST /workspace/skill/upload`（需供应会话），替换本地投放。
- ~~**口令哈希**~~ 已落地（2026-10-08）：PBKDF2-SHA256 + 独立盐，存 `config/roles.json`（§13 Phase 5）。
- **多进程部署**：InMemoryMessageBus → RedisMessageBus；scheduler 单副本开关。
- **细粒度授权**：agent 内的工具/知识库级授权（用户/智能体管理界面已随 Phase 5 落地，工具/KB 级仍待做）。
- **知识库共享（P2）**：`share_policy.py` 对 `ResourceKind.KNOWLEDGE_BASE`（框架原生支持，access/_policy.py:28）放行 admin 名下共享库，语义对齐 §13 Phase 4；配套 `.env` 规则 `AGENTS_KB_DEFAULT_SHARE` / `AGENTS_KB_SHARE`。
- **知识库内容流水线（P2）**：`knowledge/<库名>/**` 母版（文件即事实源）+ `scripts/sync_knowledge.py` 幂等上传/清理（对齐 sync_skills.py 哲学）。
- **Agent 默认挂库（P3）**：`AGENTS_AGENT_KB=助手:库名` 经 `extra_agent_middlewares` 追加 RAGMiddleware（需冒烟验证与会话级挂载的工具重名问题）。
- **多模态 embedding（P3）**：引入图文联合向量模型后，开启 Word/PPT `include_image` 与 ImageParser。

## 11. 验证结论（历史，2026-09-23，v2.0.8 / glm-5.3-flash）

| # | 验证项 | 结果 |
|---|---|---|
| 1 | agent 注册（含 system_prompt.md 填充） | ✅ |
| 2 | 注册幂等（重跑全 PATCH） | ✅ |
| 3 | 投放后 reconcile 收编 | ✅ |
| 4 | 技能差异化（周报助手 3 / 数据质检员 2，无泄漏） | ✅ |
| 5 | 母版更新传播 | ✅ |
| 6 | prune 清理 | ✅ |
| 7 | LLM 全链路（技能感知与预期一致） | ✅ |
| 8 | 预建目录与 workspace initialize 兼容 | ✅ |

已知坑：SKILL.md 必须无 BOM（PS 5.1 `Set-Content -Encoding utf8` 会写 BOM）；`PATCH /sessions/{sid}`、`GET /sessions/{sid}/messages` 需要 `agent_id` query 参数。

## 12. 迁移记录（历史，2026-09-24，提交④+fe0c7e1）

1. **装配层迁入 `server/main.py`**：官方示例逐字节迁移（提交②原样可对照），改造三处——basedir 指向本工程 `workspaces/`、`extra_credentials`、uvicorn 入口 `server.main:app`（win32 禁 reload 不变）。
2. **模型收敛**：见 §8。
3. **web_ui vendor**：提交③，来源 agentscope v2.0.8 @ 提交③信息所记克隆 HEAD，代码零改动。
4. **数据与杂项收敛**：`workspaces/ blobs/ logs/ .env` 随迁（gitignore）；根验证脚本并入 `scripts/checks/`；`requirements.txt` 入库；工作区根启动脚本移除；`.vscode/launch.json` 留工作区更新。
5. **迁移后全套验证通过**：模型目录收敛（10 个存量会话修复，其中 2 个曾遭 UI 错配 o4-mini）、注册/投放/验证全绿、LLM 全链路、克隆 `git status` 归零、5 个提交推送 codeup。

## 13. 多租户加固记录（2026-09-28，提交 b19400e / 7fa9a3f / dcefa32 / fa6c963）

四个阶段独立提交，各自验证通过后推送。

### Phase 1 — 用户间输出隔离（b19400e）

- `server/per_user_workspace.py`：`PerUserLocalWorkspaceManager`，workdir 收窄为
  `workspaces/<agent_id>/users/<user_id>/`；user_id 来自外部 header，经
  `safe_user_component` 净化（白名单字符 + 截断）防路径注入；workspace_id、
  TTL 缓存、团队 worker 绑定语义全部复用父类（团队同 user，不受影响）。
- 隔离范围随 workdir 自动扩大：长期记忆（Memory/）、会话产物、生成文件逐用户私有；
  技能分区变为每用户独立副本。
- 脚本层多用户化：registry v2（`name -> {user -> {agent_id}}`，agent 是用户隔离
  资源，各用户注册自己名下副本）；register/sync/validate/configure_model/chat_smoke
  均支持 `--user` 与逐 (user, agent) 迭代；`common.api` 支持 per-call user。
- 存量归档：`workspaces/<agent>/* -> users/admin/`（零数据丢失）。

### Phase 2 — 共享技能自动播种 + Redis 持久化加固（7fa9a3f）

- 装配 `skill_paths=skills/ 共享母版`：新用户工作区创建即播种 `.seed`，首次触达
  自动装备——新用户零操作获得共享技能（u3 实证）；差异化技能不在播种范围。
- `sync_skills.py` 转型：覆写 `.seed`（旧分区重装备也能拿到新版）+ 增量覆盖已装备
  分区 + `--prune`；母版更新标记实测传播至 .seed 与全部用户分区。
- **Redis 持久化加固**（实施中发现的实证事故）：原容器 RDB-only 且无卷，停启循环
  丢失了 u2 的最近写入。迁移为命名卷 `agentscope_redis_data` + `--appendonly yes`
  （先无 AOF 装载 RDB，运行时 `config set appendonly yes` 生成 AOF，再以正式参数
  重建容器）；`start-all.ps1` 的创建命令同步更新。

### Phase 3 — HTTP Basic 全链路认证（dcefa32）

- `server/auth.py`：用户表 `.env` `AGENTS_USERS=admin:pw,...`（gitignore，明文）；
  `install_basic_auth(app)` 经 FastAPI `dependency_overrides` 替换
  `get_current_user_id`——全站端点要求 `Authorization: Basic`，校验失败 401；
  `AGENTS_USERS` 未配置时自动退回头行为（开关语义）。
- 前端（vendored）：setup 页新增 Password 字段（en/zh）；`client.ts` 注入 Basic 头
  （fetch-SSE 天然可带头）；两处上传 XHR 同步；App 门控要求凭据存在，旧用户回
  setup 补录一次。下载沿用 capability token（无法带头的浏览器原生请求）。
- `scripts/common.py`：`api()` 按 `AGENTS_USERS` 自动附带 Basic——脚本与后端共用
  同一凭据源；`scripts/_auth_probe.py` 为 401/401/200 认证矩阵探针。
- 验证矩阵：无凭据 401 / 错密 401 / 正确 200；validate_setup（admin/u2/u3）全过；
  chat_smoke（Basic 鉴权下）全链路通过。

### Phase 4 — 跨用户共享 agent：单一所有权 + 授权（2026-09-28）

模型变更（副本模式废弃）：

- 所有 agent 归属唯一 owner（`AGENTS_OWNER=admin`）；`register_agents.py`
  回归单身份注册，registry 回到 `name → {agent_id}`，`--user` 副本逻辑移除。
- `server/share_policy.py`：`OwnedAgentsSharePolicy`——
  `AGENTS_DEFAULT_SHARE=个人助手` 对所有人可见（READ）；
  `AGENTS_AGENT_SHARE=周报助手:u2;数据质检员:u3` 显式授权（READ）；
  非 AGENT 类型与未配置 `AGENTS_OWNER` 时维持 DenyAll。
- `users/<user>` workdir 段由冗余转为**共享模式下的唯一文件隔离层**（同一
  agent_id 被多用户访问），实测 admin/u2 同 agent 工作区并存且互不可见。
- 脚本适配：sync 的用户清单 = `AGENTS_USERS` 键 ∪ `users/*` 已存在目录；
  validate 断言可见性矩阵（含负向泄漏检查）与共享隔离；chat_smoke 按名解析
  可见 agent；configure_model 对不可见 agent 的 404 跳过。
- 副本清理：删除 u2/u3 名下 4 个 agent 副本与孤儿工作区。
- 验证：可见性矩阵（admin 3 可编辑 / u2 2 只读 / u3 2 只读 + 负向不泄漏）、
  共享周报助手下双用户 workdir 隔离、u2 全链路对话全过。
- **已知限制（安全边界声明）**：共享模式下本地工作区无文件围栏——u2 已实测借
  agent 工具读取 `users/admin/` 目录（本地后端按"调用者可信"设计，无路径边界，
  权限规则不构成安全边界）。文件级多租户隔离需迁移沙箱后端（§10 优先项）。

### Phase 5 — 用户管理与 RBAC：roles.json 单一事实源（2026-10-08）

认证与授权机制整体升级（迁移计划与实施记录见 `docs/migration-plan-user-management.md`）：

- **认证**：用户表从 `.env` `AGENTS_USERS`（明文）迁移至 `config/roles.json`
  （PBKDF2-SHA256 哈希 `sha256$<salt>$<hash>`，每用户独立盐）。首启引导
  `bootstrap_roles_file()` 从 .env 导入并当场哈希化，之后 .env 用户表不参与认证；
  保留无 `sha256$` 前缀的明文比对兼容分支（回滚窗口期）。
- **授权**：`OwnedAgentsSharePolicy`（.env `AGENTS_OWNER` / `AGENTS_DEFAULT_SHARE`
  / `AGENTS_AGENT_SHARE`）替换为 `RoleBasedAccessPolicy`（`role_access_policy.py`）
  ——「归属即规则」：admin 名下 = 公共资源（KB/凭证全员只读、智能体按
  `user_agents` 白名单），用户自建 = 私有（owner + admin）；`admin_edit` 决定
  admin 对他人资源的读写；roles.json 5 秒缓存热读取，改配置无需重启。
- **管理**：`roles_admin_api.py` 提供 GET/POST `/roles`（仅 admin；保存时明文
  自动哈希化、返回时密码置空）；前端新增 `/team` 用户管理页（角色/团队/白名单/
  密码重置），侧边栏仅 admin 可见。
- **存量转换**：`scripts/migrate_env_share_to_roles.py` 将 .env 共享授权并入
  `user_agents`（按名称→agent_id，幂等，需后端运行后执行）。
- **遗留**：`share_policy.py` 保留但不再被引用（回滚窗口后移除）；roles.json 中
  旧共享键（kb_shared_owners 等）已废弃、读取时忽略。

### Phase 6 — 评审修复：生命周期 / 校验 / 代操作 / 改名（2026-10-10）

RBAC 评审（`docs/rbac-review.md`，4 设计缺口 + 3 bug）修复，方案见
`docs/rbac-fix-plan.md`（P0–P4）。除下列三段定义外，还包括：roles.json
损坏显式 500（不再静默返回空骨架）；保存/改名全程文件锁
`roles.json.lock`（O_CREAT|O_EXCL + 崩溃残留过期兜底）；前端未保存
变更计数与刷新拦截；admin 判定统一为 `auth.get_user_role()` + `GET /me`
（前端不再硬编码用户名）；脚本认证收敛为 admin Basic
（`AGENTSCOPE_PASSWORD`）+ `X-On-Behalf-Of` 代操作（仅 role=admin 且
`admin_edit` 生效，资源归属记为被代用户——密码哈希化后逐用户身份
场景的唯一通路），`.env` 用户表相关脚本路径全部下线。

**删除用户行为定义**：`POST /roles` 保存时 diff 出被删用户并清点其
agent / KB / 凭证——名下无资源直接保存；有资源返回 **409 + 资源清单**，
前端弹窗三选一：①转移给 admin（推荐，落入「归属即规则」：agent 按
`user_agents` 白名单可见、KB/凭证全员只读，原 owner 会话随迁移级联清理）；
②连带删除（不可逆，二次确认）；③取消保存。磁盘 `workspaces/<agent>/users/<user>/`
**不自动删**：保存响应带回目录清单，由 `scripts/archive_user_workspace.py`
改名 `<user>.deleted-<时间戳>` 归档（可人工恢复）；历史幻影资源用
`scripts/reclaim_orphan_resources.py` 一次性扫描（SCAN `agentscope:user:*`，
owner ∉ roles.json）批量转给 admin。

**「用户名即路径组件」约定**：用户名同时充当 roles.json 键、Redis owner、
磁盘目录名 `users/<user>/` 三重主键。入口白名单 `^[A-Za-z0-9_-]{1,64}$`
（前后端同规则，`validate_username`；`admin` 键豁免）保证「净化前即合法、
净化无碰撞」，`per_user_workspace.safe_user_component` 净化降级为纯兜底
双层防御——杜绝 `a/b` 与 `a:b` 碰撞同一目录的隔离穿透。改名走独立接口
`POST /roles/users/rename`（不复用全量保存：换键与删旧建新服务端无法
区分），按 **Redis owner → roles.json（生效点）→ 磁盘目录** 顺序迁移，
失败可幂等重试且不产生幻影资源；`admin` 不可改名。

**用户表事实源**：`config/roles.json` 是唯一事实源（users / user_agents /
admin_edit）；`.env` 的 `AGENTS_USERS`、`AGENTS_OWNER`、
`AGENTS_DEFAULT_SHARE`、`AGENTS_AGENT_SHARE` 全部废弃，仅
`bootstrap_roles_file()` 首启导入和迁移脚本读取。
> 变更（2026-10-12，Phase 7）：事实源已迁 Redis，roles.json 降级为
> 「首启引导种子 + 可导出备份」，本段与上方三步改名迁移均被 Phase 7 取代。

### Phase 7 — 用户表迁 Redis + 不可变 id 换键（2026-10-12）

方案与风险闭环见 `docs/rbac-fix-plan.md` 第九章（9.1–9.9），落地形态：

- **存储收口**：新增 `server/roles_store.py` 唯一存储入口，双实现——
  `RedisRolesStore`（现役；键族 `roles:users` / `roles:name` /
  `roles:user_agents` / `roles:config` / `roles:ver` /
  `roles:pending_disk`）与 `FileRolesStore`（`AGENTS_ROLES_STORE=file`
  回滚开关，保留旧 roles.json 全部语义）。`config/roles.json` 降级为
  **首启引导种子**（Redis 空 + 文件存在 → 导入后改名留档）与
  **可导出备份**（`GET /roles/export` 落 `config/role_backups/`，
  即现成回滚包——回滚依赖定时冷备而非现场导出）。
- **不可变 id**：用户主键从登录名换为 `u_<base58>` 随机 id（admin
  字面量豁免；迁移/文件模式按 blake2b 确定性派生，重跑与回滚恒等）。
  资源 owner、`user_agents`、磁盘目录全部改挂 id——**改名退化为一次
  登录名改写**（Lua 原子改 `users.name` + 维护 `roles:name` 索引 +
  INCR ver）；Phase 6 的三步改名迁移与 `roles.json.lock` 文件锁废除，
  `POST /roles/users/rename` 契约不变，磁盘目录以 id 净化名命名。
- **并发**：保存从文件锁改为 **WATCH 版本号 CAS**（`POST /roles` 读时
  记 `roles:ver`、写时校验，被抢先返回 409 请刷新）；多实例缓存失效
  用版本键轮询（每请求 1 次 GET）。
- **降级链（9.6）**：Redis → 内存缓存 → `config/roles.snapshot.json`
  本地快照 → fail closed（仅放行 bootstrap admin）。降级窗口可登录、
  可鉴权，全部用户写操作 503；进程重启后从快照恢复降级服务。
- **持久化护栏（9.9）**：compose 的 redis 写死 `--appendonly yes
  --appendfsync everysec`；启动自检序列 `PING → INFO 查 AOF → 补偿
  队列重试 → 首启种子导入 → 快照预热`，AOF 未开打 WARNING 横幅并
  拒绝用户写操作（fail closed）；每日定时走 export 链路落冷备（轮转
  30 份）；磁盘目录改名失败 LPUSH `roles:pending_disk` 补偿队列，
  后台每 5 min 幂等重试自动收敛，队列长度在 health 暴露、非 0 告警。
- **可观测**：`GET /roles/health`（连通性 / AOF / 冷备时间 / 补偿
  队列长度）+ `config/roles_audit.log` 审计日志（谁、何时、改了什么）；
  `HGETALL` 仅限全量重建与 export（big-key 约束，鉴权路径只 HGET
  单点查询，约束固化在 store 内部）。
- **迁移**：`scripts/migrate_users_to_redis.py`——ids / redis / owner /
  disk 四步各自幂等、可 `--step` 单独执行；`--dry-run` 零写入演练；
  顺序守卫（Redis 已有用户数据即拒绝，防二次搬家）；迁移后自动校验
  （旧名 owner 键残留=0、磁盘旧目录遗留=0、name↔id 双向一致）输出
  报告，通过后旧 roles.json 自动改名 `.pre-migration` 留档。

## 14. 知识库落地记录（2026-09-29）

结论先行：**知识库从"装配了但不可用"（内存向量库 + 不可用的 OpenAI embedding 卡片）
升级为"Qdrant 容器持久化 + 本地 BGE embedding"的全链路可用状态**，含中文语义检索
与会话级挂库对话（RAG grounding 实测 PASS）。全部走原生扩展点，本体零改动。

### 14.1 架构与装配点

```
FastAPI(8000) ──→ Qdrant 容器 agentscope-qdrant (127.0.0.1:6333, 卷 agentscope_qdrant_data)
       │           镜像 qdrant/qdrant:v1.19.1（与 qdrant-client 1.19.1 版本对齐；
       │           替代原 QdrantStore(":memory:")，重启即丢）
       ├──→ TEI 容器 agentscope-tei (127.0.0.1:19556→容器80)
       │           镜像 ghcr.io/huggingface/text-embeddings-inference:cpu-1.8
       │           --model-id /data/BAAI--bge-small-zh-v1.5（models/bge/ 只读挂载，candle CPU 后端）
       └──→ 索引流水线（进程内 IndexTaskConsumer/IndexWorker，enable_index_worker 默认开）
            blob(本地) → Parser → ApproxTokenChunker → TEI /v1/embeddings → Qdrant upsert

后端进程由 backend-watchdog.ps1 托管（start-all 启动的是看门狗）：
进程死亡或"失聪"（见 §14.3-8）均在秒级自动重启，索引任务由 sweeper 续跑。
```

- `server/main.py`：`QdrantStore(url="http://localhost:6333")`；
  `knowledge_parsers=[TextParser, PDFParser, WordParser(include_image=False),
  ExcelParser, PPTParser(include_image=False)]`（ImageParser 不启用——它不做
  OCR，只是把图片原样交给多模态 embedding，纯文本模型必挂）；
  `extra_credentials=[ZhipuCodingPlanCredential, LocalBgeCredential]`。
- `server/model_policy.py` 新增（复刻 §8 收敛模式）：
  `BgeEmbeddingModel(OpenAIEmbeddingModel)`——`list_models()` 读
  `models/bge/*.yaml`，且 `pass_dimensions=False`（TEI 对非 Matryoshka 模型
  拒绝 dimensions 参数）；`LocalBgeCredential`（type=`local_bge`，api_key 占位
  `local`，base_url 指向 TEI）；`ZhipuPlanEmbeddingModel`——空目录，绑到
  `ZhipuCodingPlanCredential.get_embedding_model_class()`，否则 UI 建库页会
  泄漏 OpenAI 内置 embedding 卡片（端点不支持，§8.1 同款问题的 embedding 版）。
- KB 与会话的绑定是**会话级**：session 配置 `knowledge_config.knowledge_base_ids`
  → ChatService 运行时挂 `RAGMiddleware`（app/_service/_chat.py:187），agent
  获得 `search_knowledge` 工具。
- 模型卡片 `models/bge/bge-small-zh-v1.5.yaml`（dimensions 512 / context_size
  512）；权重自 192.168.100.100:/home/agentrunner/models/BAAI--bge-small-zh-v1.5
  拷贝（HF cache 布局取 snapshots/master/，剔除重复的 pytorch_model.bin，保留
  model.safetensors ≈91MB）；`.gitattributes` 对 `models/bge/**` 的
  safetensors/bin/onnx/h5/msgpack 启用 Git LFS。

### 14.2 脚本层

- `scripts/configure_embedding.py`：对 `AGENTS_USERS` 全部用户幂等写
  `local_bge` 凭据（`.env` `TEI_BASE_URL` 可覆盖端点，默认
  `http://localhost:19556/v1`）；base_url 变更时删旧建新。
- `scripts/kb_smoke.py`：Qdrant/TEI 探针 → embedding 目录断言（仅 local_bge、
  512 维、智谱不泄漏）→ 建临时库 → multipart 上传中文 txt → 轮询索引至
  ready → 语义检索命中断言 → 删除清理。
- `scripts/validate_setup.py` 新增 KB 段：Qdrant/TEI 可达性（失败=FAIL）+
  embedding 目录断言（缺 bge 卡片=HINT 提示跑 configure_embedding）。

### 14.3 实施坑（复发警惕）

1. **PS 5.1 + 无 BOM 的 .ps1 中文注释 = 解析错误**：PS 5.1 按 ANSI/GBK 误读
   UTF-8 中文注释，多字节序列可吞掉后续结构字符（本次 `} else {` 报意外
   标记）。.ps1 里新增注释一律用 ASCII；"UTF-8 无 BOM"铁律仅适用于 SKILL.md。
2. **httpx 继承 Windows 系统代理且不理会其 localhost 旁路**：Clash Verge 写
   WinINET 代理后，httpx（qdrant-client/openai SDK 底座）`trust_env=True` 把
   127.0.0.1:6333 的请求发给 Clash → 502（urllib/裸 socket 因 proxy_bypass
   直连不受影响）。修复：`server/main.py` 入口强制 `NO_PROXY/no_proxy` 含
   `localhost,127.0.0.1`。
3. **TEI cpu-1.8 镜像默认监听 80**（非 8080）——端口映射须 `19556:80`。
4. **PowerShell 参数内嵌 `(Join-Path ...)` 表达式**传给 docker 无效
   （invalid reference format）——用字符串插值 `"$Root\models\bge:/data:ro"`。
5. **Docker 拉镜像三件套**：daemon 级镜像源（轩辕免费节点 403 限流）、ghcr.io
   直连被断、国内镜像站走代理反而 EOF。最终组合：Docker Desktop
   settings-store.json 手动代理 127.0.0.1:7897 + `OverrideProxyExclude` 排除
   国内镜像站 + TEI 从 `ghcr.nju.edu.cn` 拉取后 `docker tag` 回官方名。
6. **客户端批大小 vs TEI 校验上限**：框架 OpenAI embedding 默认一批 2048 条，
   TEI OpenAI 兼容路由默认 `--max-client-batch-size 32`——大文档索引必撞
   413 "batch size 397 > maximum allowed batch size 32"。修复：
   `BgeEmbeddingModel._TEXT_BATCH_SIZE = 32`。
7. **context_size 只是元数据，框架从不截断**：TEI 按模型真实上限校验单条输入
   （bge < 512 token），而 ApproxTokenChunker 的"近似 token"对 CJK 与点线
   （`....`/`……`）严重低估——同一份 GB 规范实测同尺寸分块 92~1268 token，
   字符成本线性估算（两轮系数）都无法兜底。最终方案：`_call_api` 先批量问
   TEI `/tokenize` 拿真实计数，超限分块按比例裁剪并复测（≤3 轮收敛）；
   `/tokenize` 不可用时回退保守字符截断。
8. **Windows IOCP proactor accept 失聪**：客户端在 accept 窗口期重置连接时，
   `IocpProactor.accept_coro` 抛 OSError 64（"Accept failed on a socket"）且
   **asyncio 直接关闭监听 socket**——进程活着但永久拒绝新连接（Python
   3.13.12 仍复现）。进程级看门狗发现不了（进程未退出），故
   `backend-watchdog.ps1` 为**健康监督**模式：`/health` 连续 ~40s 连不上
   （启动期豁免）即杀子进程重启；被中断的索引任务由 IndexSweeper 续跑。
9. **qdrant-client 与 server 版本差须 ≤1 个 minor**：client 1.19.1 配
   server 1.15.1 每次请求都告警（不兼容风险），server 镜像随之升到 v1.19.1
   （命名卷数据跨版本自动迁移）。
10. **glm 思考模型的隐藏推理耗尽 max_tokens → 空 content**：glm-5.3-flash
    直接 API 调用（视觉页注）默认带隐藏推理，表格页任务重时推理耗尽
    `max_tokens`，`content` 为空字符串——调用方静默拿到 None。修复：
    请求体显式 `"thinking": {"type": "disabled"}` + 空 content 视为
    瞬时失败进入退避重试。
11. **知识库换血漏删旧文档 → 新旧并存双倍污染**：本轮典型页库换血脚本
    只建库上传、漏了删除旧文档，检索 top8 全部来自旧文档（含乱码正文），
    新文档向量被淹没。教训：换血脚本必须"上传新 → 删旧 → 校验检索结果
    的 document_id 唯一"；framework 的 delete_document 向量清理本身
    工作正常（删后检索即干净，实测复现）。
12. **Streamdown 行内单 `$` 数学默认关闭**：`@streamdown/math` 的
    `singleDollarTextMath` 默认 false，回答/页注里的行内变量
    （`$K_m$`）原样露出 `$`。修复：markdown 组件
    `createMathPlugin({ singleDollarTextMath: true })` + 引入
    `katex/dist/katex.min.css`（Streamdown 不自动注入样式）。
13. **API 创建的会话必须显式设置 chat_model_config**：前端建会话时会
    自动注入模型选择，API 脚本不设则聊天运行时模型解析失败（404），
    被归类为 "invalid_request"——极具迷惑性（看似模型端点故障，实则
    会话没配模型）。e2e 脚本建会话务必带上 chat_model_config
    （zhipu_coding_plan 凭据 + glm-5.3-flash）。
14. **单行 `$$…$$` 被 remark-math 判为行内数学 → `\tag` 渲染失败**：
    KaTeX 的 `\tag` 只允许 display 模式；行内渲染（displayMode=false）
    直接抛错，rehype-katex 回退显示原文——截图上就是裸 LaTeX。
    触发条件：公式写成单行 `$$…$$`（`$$` 不独占一行）。修复：
    ① 页注入库前经 `_normalise_math_blocks()` 规范化为 flow 格式
    （`$$` 各占一行，幂等）；② PAGE_SCAN_PROMPT 与智能体
    system_prompt 同步约定"$$ 各占一行"。注意 Flow 判定必须在
    strip 之前看原始 inner 是否含换行（strip 后误判会导致重复包裹、
    非幂等）。

### 14.4 验证结论（2026-09-29）

| # | 验证项 | 结果 |
|---|---|---|
| 1 | 模型目录收敛（bge 卡片 512 维 / 智谱 embedding 目录为空 / chat 目录不变） | ✅ |
| 2 | kb_smoke 全链路（建库→中文索引 ready→语义检索命中→清理） | ✅ |
| 3 | 持久化（Qdrant 容器重启后 KB 记录与 collection 仍在） | ✅ |
| 4 | RAG 端到端（会话挂库对话，回答逐字命中库内事实，grounding PASS） | ✅ |
| 5 | validate_setup（可见性矩阵 + 隔离 + 技能 + KB 基础设施断言） | ✅ |
| 6 | start-all/stop-all 编排幂等（六端口健康检查全绿） | ✅ |
| 7 | 大文档索引：19.5MB GB50028-2006 PDF → ready（397 分块），条文级检索命中 | ✅ |
| 8 | 插图/表格/公式页注（§14.5）：522 分块，公式 LaTeX 转写与真值一致、表格 Markdown 结构化命中 | ✅ |

已知边界：知识库归创建用户私有（跨用户共享见 §10 P2 路线）；embedding 维度
钉死（换模型=重建库重灌）；TEI CPU 推理 batch 上限 4、吞吐有限（19.5MB
PDF 索引约数分钟）；分块按 approx_token 512/50 切、超长分块在嵌入前被
/tokenize 精确裁剪（被裁尾部仍留在分块原文中，只是不参与向量）。

### 14.5 插图/表格/公式处理：VLM 统一页注（2026-10-08，同日二次迭代）

问题：扫描版规范 PDF（实测 GB50028-2006：329 页 = 329 张整页 JBIG2 扫描图，
文字层只有条文正文）的插图、图题、数据表格、**公式**完全不在索引里——
检索"示意图"零命中；公式被出版社 OCR 线性化成乱码（`QII=iQa`、分数堆叠
拍平成相邻行、下标误读），RAG 的 LLM 拿着乱码重组公式产生似是而非的转写。
pypdf 只抽文字且解 JBIG2 需外部 jbig2dec 二进制。

方案：`server/pdf_figures_parser.py` 的 `PdfWithFiguresParser`（ParserBase
子类）替代 `PDFParser` 注册，正文抽取 + **统一页注**一体：

- **归一化层（格式兼容核心）**：MuPDF 库内解码全部 PDF 图像编码
  （JBIG2/CCITT/JPX/DCT/Flate）与色彩空间（CMYK/ICC/Indexed → RGB）、
  SMask 摊平，统一出口 RGB PNG——原始流字节永不透传；PIL 兜底解码；
  <100px 装饰图过滤、最长边 ≤1600 降采样。VLM 只见普通 PNG，源格式透明。
- **统一页注（一次 VLM 调用查三类）**：整页扫描提示词一次覆盖
  **插图 + 表格 + 公式**——模型自判"无"则不产 Section（329 页中 254 页
  纯文字被过滤，75 页产出页注）；独立裁剪图走 FIGURE_PROMPT。不做
  文字层正则检测（文字层是待修复对象，不可作为路由依据）；整页扫描
  判定：`get_image_rects` 面积 ≥0.8 页面积。提示词去特例化——不绑定
  GB 编号惯例（公式编号保留原编号、无编号不造）、不绑定"式中"用语。
- **表格 Markdown 转写**：表头 + 全部可见行列转 Markdown（超长表前
  20 行 + "（其余行略）"），`max_tokens` 提至 1200——表格从 150 字
  散文摘要升级为可检索、可引用的结构化数据。
- **公式 LaTeX 转写**：编号保留原编号，连同符号含义一并转写——检索
  同时命中乱码正文与干净转写，LLM 优先采信后者。
- **工程化**：`thinking: disabled`（见坑 10）；xref 去重（同图多页引用
  只调一次 VLM）；`FIG_THREADS=3` 并发；429/5xx/空 content 退避重试；
  单图失败跳过不阻塞文档索引；每页合并为单个 `[页注·页N]` Section
  （kind `page_note`）。
- 依赖：`pymupdf==1.28.2`（MuPDF 内置 JBIG2 解码，选它弃 pypdf 的主因）、
  `pillow==12.3.0`。

格式兼容验证矩阵固化为 `scripts/checks/pdf_figures_matrix.py`
（PNG/JPEG 彩图、50px 装饰图过滤、2400px 降采样、纯文字页过滤、坏 xref
跳过路径），全过。

验证（GB50028-2006 全量重传，2026-10-09 页注即正文 + flow 格式后）：
ready 812 分块；公式页（19 个）仅剩页注、零公式碎片；页 57 页注
flow 格式（`$$` 独占一行）含完整 6.2.1~6.2.4 条文与公式；SSR 渲染
katex-display 正常、katex-error=0；检索公式查询 top1 为干净页注。
典型页测试库（49 页）同步验证：96 分块 = 35 正文 + 61 页注，
flow 格式全过，SSR 渲染通过。

坑：PAGE_SCAN_PROMPT 字符串内嵌 ASCII 双引号截断字面量（改用【】）；
提示词不得绑定特定文档的编号惯例；Word/PPT 的 `include_image` 仍关——
其插图接入同一页注管线为二期。

三次迭代后的最终形态（同日）：

- **公式页"页注即正文"**：页注含公式转写（`$$`）且 `finish_reason !=
  "length"` 的页，其 OCR 正文 Section 不入库（文字层公式必然乱码，
  VLM 整页转写经实测优于 OCR）；转写失败/截断的页自动回退文字层，
  宁要乱码不要缺页。提示词相应改为"完整转写本页全部内容"（条文保持
  原文勿概括、表格全行列 Markdown、公式 LaTeX），并**移除
  `max_tokens` 上限**——长转写由下游分块器自然切分、嵌入期 `/tokenize`
  裁剪兜底，生成端不做人为限流。
- **渲染层**（web_ui vendored 修改）：`components/markdown/index.tsx`
  改用 `createMathPlugin({ singleDollarTextMath: true })` 并引入
  `katex/dist/katex.min.css`（`@streamdown/math` 依赖已含 katex）——
  Streamdown 默认关闭行内单 `$` 数学，导致回答/页注里的行内变量
  （`$K_m$`）原样露出 `$`。聊天回答、知识库工具详情、资源详情三个
  视图共用该组件，一处修复同时生效。已知权衡：正文中美元金额可能被
  误判为行内公式起始（本场景几乎不涉及）。
- **典型页测试库**：`燃气规范典型页`（49 页 = 18 公式页全保留 + 图表页
  抽样 + 3 纯文字对照；选取依据 = 现有索引 chunk 的 metadata.page +
  kind==page_note）。测试资产 `logs/spec_typical.pdf` 与
  `logs/_make_typical_pdf.py`（不入库）。
- **符号说明的行内数学（约定反转，2026-10-09）**：页注符号说明中的
  变量用行内数学 `$…$` 包裹（如 `$Q_h$——燃气小时计算流量`），前端
  KaTeX 渲染为公式。此为对早前"符号说明不用 $"约定的反转——当时
  渲染层不支持行内数学，`$` 原样露出；`singleDollarTextMath` 修复
  后约定随之反转，`_clean_math_delimiters` 的行内拆壳逻辑同步移除
  （仅保留 `$$$$` 折叠）。
- **插图可见性（2026-10-09）**：VLM 页注判定含插图/示意图的页
  （提示词约定：回答开头输出【含插图】标记），解析器将该页快照 PNG
  落盘至 `blobs/figures/<uuid>/`（`FIGURES_DIR` 可配），并在页注尾部
  追加 markdown 图片链接（`![插图·原文第N页](FIG_PUBLIC_BASE/figures/…)`，
  `FIG_PUBLIC_BASE` 默认 `http://localhost:8000`）。服务层
  `server/figures_router.py` 提供无鉴权静态路由（`<img>` 无法携带
  Basic 头，本地部署的信任边界内可接受；对外暴露需改 download-token）。
  前端 Streamdown 原生渲染 markdown 图片（`<img>` 已验证），知识库
  详情/资源详情视图直接显示原插图。文档删除时 figures 目录不联动
  （孤儿图仅占磁盘）。已知行为：聊天回答是否内嵌图链接属 agent
  自主行为（实测会检索到链接；个别运行 agent 走 browser-use 打开
  URL 导致长运行）——保证展示的兜底是知识库详情视图。
- **回答端公式格式不设提示词限定（实验结论）**：曾给三个智能体
  system_prompt 增加"公式必须带 $$/$ 定界符"条款，后发现**不必要**——
  页注本身是格式规范的完整转写，检索命中后回答自然跟随其样式
  （实测移除限定后回答仍保留 $$ 块公式、无裸 LaTeX）。数据层
  （chunk 组织）是起决定作用的一层，提示词不做跨 agent 同步的
  格式限定。

补充（同日）：符号说明的行内 `$...$` 定界符噪音——提示词增加输出约定
（公式用 $$ 块、普通叙述不用 $）+ `_clean_math_delimiters()` 确定性
后处理（保护 $$ 块、拆行内壳、清游离 $）。测试阶段以"典型页测试库"
验证：从现有索引反推典型页（chunk metadata.page + kind==page_note，
49 页 = 18 公式页全保留 + 图表页抽样 + 3 纯文字对照），实测 47 页页注、
$ 噪音零残留；测试资产 `logs/spec_typical.pdf`（不入库）。
