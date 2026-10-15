# AGENTS.md

## 背景

`agentscop-platform` 是「官方架构 + 业务」的合并工程：基座为官方
AgentScope **v2.0.10**（本地源码仓库 `../agentscope-official`，editable
安装，业务对官方源码**零改动**），业务层由两份历史工程合并而来：

- `agentscope_extra-master`（骨架：RBAC 认证 / 按用户工作区 / PDF 视觉
  解析 / 单端口托管 / 部署物料）
- `agent-new`（平台补丁：动态模型发现 / 技能市场 / 会话与 KB 增强 /
  候选词 / 僵死自愈等）

架构与机制详见 `docs/design.md`（机制设计与历史决策）。

## 约束

- **禁止改动** `../agentscope-official` 下任何文件；一切定制在本工程内，
  且只走框架原生扩展点（`create_app` 参数）或对官方半公开 API 打补丁
  （集中注册在 `server/app.py`，各补丁独立成模块、互不依赖）。
- **存储模式双轨**：本地默认 `AGENTS_STORAGE=sqlite`（零外部依赖）；
  生产 `redis`。凡直接读 SQLite 的补丁（remote_models、embedding_models、
  skill_visibility、session_permission、stuck_run_guard、
  kb_credential_fallback）都必须带 redis 守卫/降级（见各模块）。
  路径一律取自 `server/paths.py`，禁止自行推导相对路径。
- SKILL.md 必须 UTF-8 **无 BOM**；同一 agent 收到的技能 frontmatter
  `name` 不得重复。
- skill 母版/智能体声明的修改**不会自动生效**：改后必须跑
  `scripts/sync_skills.py`（移除加 `--prune`）与
  `scripts/register_agents.py`；文件是唯一事实源，重跑会覆盖 UI 手改。
- **.ps1 新增注释一律 ASCII**：PS 5.1 按 ANSI/GBK 解析无 BOM 脚本，
  中文注释可吞掉后续结构字符（design.md §14.3-1）。
- **模型大文件走 Git LFS**（`.gitattributes` 已配置 `models/bge/**`）；
  clone 后先 `git lfs pull`，否则 TEI 容器读到的权重是指针文件。
- 安全：本地工作区**无文件围栏**（会话可读写宿主任意路径），勿存放
  敏感数据；口令/密钥只在 gitignore 文件中（`.env`、
  `config/roles.json`——仅存 PBKDF2 口令哈希 `sha256$<salt>$<hash>`），
  不得写入任何入库文件。
- `workspaces/ data/ logs/` 为运行数据（gitignore）；`registry.json`
  是生成物，勿手工编辑。
- 资源可见性走 RBAC「归属即规则」（`server/role_access_policy.py`，
  配置 `config/roles.json`，管理页 `/team` 维护、热生效）：admin
  名下=公共资源（KB/凭证全员只读，智能体按 `user_agents` 白名单），
  用户自建=私有（owner+admin）；`admin_edit` 决定 admin 对他人资源的
  读写。

## 索引

| 路径 | 内容 |
|---|---|
| `main.py` | 启动入口（uvicorn `server.app:app`，TLS/热重载开关） |
| `server/app.py` | 装配层（`create_app` 全部扩展点 + 14 个业务接线，全部业务改动的汇聚点） |
| `server/paths.py` | 路径与部署模式开关（sqlite/redis、qdrant path/url） |
| `server/auth.py` `roles_store.py` `roles_admin_api.py` | Basic 认证 / 用户表（redis:*/file 双模式）/ `/roles` 管理 API |
| `server/model_policy.py` `zhipu_credential.py` | 智谱 Coding Plan 凭据 / 本地 BGE 凭据 / 通用智谱凭据 |
| `server/remote_models.py` `model_groups_api.py` | 远端模型动态发现 / `/model_groups` 分组查询 |
| `server/embedding_models_patch.py` `kb_reindex_api.py` `kb_credential_fallback_patch.py` | KB embedding 目录过滤 / 重索引 / 共享凭据回退 |
| `server/skill_visibility.py` `internal_skill_hub.py` `github_skill_hub.py` | 技能按角色可见 / 内部技能市场 / GitHub 技能市场 |
| `server/session_permission_patch.py` `session_replay_patch.py` `tool_offload_patch.py` `stuck_run_guard.py` | 会话权限/重放上限/工具卸载/僵死自愈 |
| `server/per_user_workspace.py` `role_access_policy.py` | 用户级工作区 / RBAC 可见性策略 |
| `server/pdf_figures_parser.py` `figures_router.py` `doc_parser.py` `quick_prompts_api.py` | PDF 视觉解析 / 插图路由 / .doc 解析 / 候选词 |
| `agents/<dir>/` | 智能体声明（agent.json + system_prompt.md + 可选 skills/） |
| `skills/`、`server/internal_skills/` | 共享技能母版（播种）/ 内部技能市场货架 |
| `models/*.yaml`、`models/bge/` | 套餐模型卡片（唯一事实源）/ BGE 权重（LFS，TEI 挂载） |
| `scripts/` | register_agents / sync_skills / validate_setup / configure_model / configure_embedding / chat_smoke / kb_smoke / backup_apps 等 |
| `web_ui/` | vendored 前端（基准 = 官方 2.0.10 examples/web_ui + 业务微调 + LAN polyfill） |
| `workspaces/` `data/` `registry.json` | 运行数据（gitignore）/ name→agent_id 映射 |
| `deploy/` | 服务器部署物料：多阶段 Dockerfile / compose（单端口 HTTPS）/ build-push.ps1（Nexus 推送）/ README 部署手册 |
| `start-all.ps1` 等 | 服务入口（本地 dev 模式：浏览器→**8000 单入口**，FastAPI 把非 API 路径反代到 Vite dev 5173 含 HMR）。部署走 dist 单端口 |
