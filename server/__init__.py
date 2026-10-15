# -*- coding: utf-8 -*-
"""agentscop-platform 业务装配包。

架构规范（对齐官方 examples/agent_service）：
- 框架能力：官方 agentscope（editable 安装自 agentscope-official），
  业务侧零改动；
- 业务扩展：本包内以「官方扩展点注入」（create_app 参数）+
  「官方公开/半公开 API 的 monkeypatch 补丁」两种形态实现，
  全部在 app.py 集中装配，模块间互不依赖（除 paths.py 与
  roles_store.py 两个基础设施）。

模块地图：
- paths.py            集中路径与部署模式开关（sqlite/redis）
- app.py              装配层（create_app + 全部业务接线）
- main.py 的职责由根 main.py 承担

业务扩展模块（extra 版骨架）：
- auth.py / roles_store.py / roles_admin_api.py   Basic 认证 + RBAC 用户表
- role_access_policy.py    "属主决定"资源可见性策略
- per_user_workspace.py    workspaces/<agent>/users/<user> 隔离
- model_policy.py          智谱 Coding Plan / 本地 BGE 凭据类型
- pdf_figures_parser.py    PDF 视觉转写解析器（含 JBIG2 扫描件）
- figures_router.py        /kb-images 插图静态路由

平台能力补丁（agent-new 版移植）：
- zhipu_credential.py          通用智谱 OpenAI 兼容凭据
- remote_models.py             远端 /models 动态发现（后台缓存）
- model_groups_api.py          /model_groups 模型分组查询
- embedding_models_patch.py    KB embedding 目录按凭据过滤
- kb_reindex_api.py            知识库文档重索引端点
- kb_credential_fallback_patch.py  共享凭据回退代理
- skill_visibility.py          技能按角色可见
- session_permission_patch.py  新会话默认权限模式
- session_replay_patch.py      会话重放上限 1000 → 100000
- tool_offload_patch.py        工具卸载阈值 10s → 45s + 事件
- stuck_run_guard.py           僵死会话自愈看门狗
- internal_skill_hub.py        内部技能市场（internal_skills/）
- github_skill_hub.py          skills.sh（GitHub）技能市场
- doc_parser.py                老版 .doc 解析（LibreOffice 转换）
- quick_prompts_api.py         候选词端点（动态占位符插值）
"""
