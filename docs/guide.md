# 核心概念与机制速览

> 给同事的一页说明：用户、agent、skill、workspace 是什么、怎么协作。
> 深入细节（含源码依据）见 `docs/design.md`；日常操作命令见 `README.md`。

## 1. 四个核心概念

| 概念 | 是什么 | 定义在哪 |
|---|---|---|
| **用户** | 登录身份（admin / u2 / u3…），HTTP Basic 认证 | `config/roles.json`（口令存 PBKDF2 哈希） |
| **agent** | 有名字、有人设、能调工具的智能体（如 周报助手） | 平台数据库（Redis）；声明源 = `agents/<目录>/` |
| **skill** | agent 可用的"技能包"：SKILL.md 说明书 + 模板/脚本 | `skills/`（共享）或 `agents/<目录>/skills/`（绑定） |
| **workspace** | 一个 (agent, 用户) 对的**私人工作目录** | `workspaces/<agent_id>/users/<用户>/` |

## 2. 所有权与可见性（谁能看到哪个资源）

可见性由 RBAC 策略决定（`server/role_access_policy.py`，配置 `config/roles.json`，
在管理页 `/team` 维护，改完即时生效、无需重启）：

- **admin 名下 = 公共资源**：知识库/模型凭证全员**只读**可见；智能体按
  `user_agents` 白名单——勾选了才能看到。
- **用户自建 = 私有资源**：仅归属者本人 + admin 可见。
- **admin**：全量可见；能否编辑他人资源由 `admin_edit` 开关决定。
- 被授权者**能用**（建会话、对话、调技能）但**不能改** agent 配置（只有 admin 能改）。
- 旧 `.env` 共享配置（`AGENTS_OWNER` / `AGENTS_DEFAULT_SHARE` / `AGENTS_AGENT_SHARE`）已废弃。

## 3. workspace：数据怎么隔离

同一个 agent 被多人使用时，每人一份私人目录，互不可见：

```
workspaces/<agent_id>/
├── users/admin/     ← admin 的全部数据：Memory(长期记忆)、skills(技能副本)、
├── users/u2/        ← u2 的同结构                       会话产物、生成的文件
└── users/u3/
```

> **⚠️ 安全边界（务必知晓）**：当前为本地工作区模式，**没有文件围栏**——
> 会话里的 agent 理论上能读写宿主任意路径（包括别人的 `users\` 目录，已实测）。
> 请勿在共享 agent 中存放敏感文件。多租户的文件隔离需切换容器沙箱后端
> （design.md §10 优先项）。

## 4. skill：目录即配置

```
skills/<技能名>/SKILL.md            → 共享技能：所有 agent、所有用户
agents/<agent目录>/skills/<技能名>/ → 绑定技能：仅该 agent
```

- SKILL.md = YAML 头（`name`/`description` 必填）+ Markdown 操作说明，
  可附带脚本与模板（例：weekly_report 带周报模板和渲染脚本）。
- **name 全局唯一**：同一 agent 收到的技能不重名；文件必须是 UTF-8 无 BOM。
- 内置探针 `sync_probe`：对话里让 agent"运行 sync_probe 技能"可自检分发链路。

### 技能怎么到达用户（三级流水线）

```
仓库母版（skills/ 与 agents/*/skills/）
   ↓ ① 工作区创建时自动播种            → 各工作区的 skills/.seed/（模板）
   ↓ ② 分区缺失时从 .seed 整体装备      → skills/<agent_id>/（生效副本）
   ↓ ③ 每次对话装配时现读分区           → agent 看到技能列表
```

- 新用户**零操作**：共享技能随工作区创建自动到位。
- **修改母版后必须跑 `sync_skills.py`**（框架"存在即跳过"，不会自动传播；
  移除母版加 `--prune`）。

## 5. 模型与认证

- **模型**：全员固定智谱 glm-5.3-flash（管理员全局收敛，用户无感、选不了别的）。
  新模型 = `models/` 加 yaml 卡片 + 重启。
- **认证**：HTTP Basic，口令表在 `config/roles.json`（PBKDF2 哈希，管理页维护；首启自动从 `.env` 引导）。无凭据/错口令 = 全站 401。

## 6. 知识库速览（2026-09-29 起）

| 件 | 是什么 | 定义在哪 |
|---|---|---|
| **Qdrant 容器** | 向量库（每库一 collection，命名卷持久化） | `start-all.ps1`，127.0.0.1:6333 |
| **TEI 容器** | 本地 embedding 服务（BAAI/bge-small-zh-v1.5，512 维） | `start-all.ps1`，127.0.0.1:19556 |
| **embedding 卡片** | 建库页唯一 embedding 选项"本地 BGE (TEI)" | `models/bge/`（权重走 Git LFS） |
| **local_bge 凭据** | 每用户一条，指向 TEI 的 OpenAI 兼容端点 | `configure_embedding.py` 写入 |

- **用法**：UI 建知识库 → 传文档（txt/md/pdf/docx/xlsx/pptx；PDF 的插图
  与表格由 glm-5.3-flash 视觉生成图注入库，扫描版同样支持）→ 建会话勾选 →
  agent 自动获得 `search_knowledge` 工具；知识库归创建者私有。
- **日常操作**：`configure_embedding.py`（新用户后跑一次）+ `kb_smoke.py`（验证）。
- **注意**：换 embedding 模型=重建库（维度钉死）；clone 后先 `git lfs pull`
  否则 TEI 起不来；详情与坑见 `docs/design.md` §14。

## 7. 日常操作速查

| 想做什么 | 操作 |
|---|---|
| 新增/修改 agent | 改 `agents/<目录>/` → `register_agents.py` |
| 新增/修改/移除 skill | 改对应目录 → `sync_skills.py`（移除加 `--prune`） |
| 新用户接入 | 管理页 `/team` 添加用户（用户名 + 初始密码）→ `configure_model.py --user X` → `configure_embedding.py --user X` → `sync_skills.py` |
| 授权 agent 给某用户 | 管理页 `/team` 勾选该用户的智能体白名单（即时生效，无需重启） |
| 验证一切正常 | `validate_setup.py`（矩阵+隔离+技能）+ `chat_smoke.py`（对话）+ `kb_smoke.py`（知识库） |

## 8. 常见坑

1. 修改 skill 母版后没跑 `sync_skills.py` → 不生效（存在即跳过）。
2. SKILL.md 带 BOM（用记事本/PS 5.1 保存易中招）→ 技能无法被识别。
3. 同一 agent 收到的技能重名 → 后投放的顶掉先前的（sync 会查重报错）。
4. 重跑注册脚本会覆盖 UI 上的手工修改——`agents/` 文件是唯一事实源。
5. .ps1 里的中文注释在 PS 5.1 下会按 GBK 误读（无 BOM）→ 新增注释用 ASCII。
6. 开着 Clash 等系统代理时，回环服务（Qdrant/TEI）必须走 `NO_PROXY` 直连——
   后端已在 `server/main.py` 入口强制，若新增回环依赖注意保持。
7. 同一知识库"删文档换血"脚本漏删旧文档 → 新旧两份并存，检索双倍
   污染（top 席位被旧分片占走）——换血后校验检索结果的 document_id
   唯一（详见 design.md §14.3-11）。
8. UI 建的会话发消息报"发往模型的请求被判定为非法" → 会话缺
   chat_model_config（前端自动注入依赖模型目录加载时机，非必现）——
   跑 `configure_model.py` 即可修复存量会话。
