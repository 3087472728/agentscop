# agents/ — 平台已有智能体的模板化导出

本目录按 `agentscope_extra-master/agents` 的模板（`agent.json` + `system_prompt.md`），
于 2026-09-30 从 `agentscope.db` 的 `agents` 表（6 条记录）导出生成。

## 说明

- `agent.json` 的 `name` / `system_prompt` 为数据库原值；`context_config` /
  `react_config` / `invite_config` 三项经比对六个 agent 完全一致（AgentScope
  框架默认值），故按模板以 `{}` 表示，未做任何改写。
- `system_prompt.md` 原样导出，其中包含服务器 Linux 路径
  （`/home/agentrunner/agentscop/...`），按「原样保留」原则未修改。
- 目录名为导出时按职能新起的英文标识（原数据无目录名概念），映射见下表。
- **表内 `agent_id` 即 `workspaces/<agent_id>/` 目录名**（数据库行主键）；
  `内部 config_id` 是 `payload.data.id`（AgentScope 框架内部配置 id），两者不同。
- 本目录是静态导出快照：agent 定义的权威数据源仍是 `agentscope.db`；
  若将来通过 API 重新注册（参照 extra-master 的 `register_agents.py`），
  平台会分配新的 agent_id。
- 各 agent 运行时安装的技能在 `workspaces/<agent_id>/skills/`（运行时数据，
  不属于 agent 定义，未复制进本目录）。

## 映射表（按创建时间排序）

| 目录 | 显示名 | 职能 | agent_id | 内部 config_id | 所有者 | 创建时间 | 提示词长度 |
|---|---|---|---|---|---|---|---|
| `assistant/` | 助手 | 通用助手（含文件交付规范） | `50d8439227cc4a71aac26f88de9df33d` | `d5dd8a9cab6f4b4abf0a8c349a15de47` | admin | 2026-08-27 07:46:32 | 427 |
| `sixnet_daily_reporter/` | 六张网 | 辽宁城市生命线「六张网」填报数据日报员 | `441da04762c147e9941db9b74eaccccf` | `0338c93feb97417180d8ca9fc25f7c8a` | admin | 2026-09-02 05:56:35 | 2121 |
| `doc_template_binding/` | 文书绑定 | 交通执法文书模板字段绑定助手 | `a129fc97883346049c3298bde0c79067` | `5a28d94c15914744b5116add92e3fd07` | admin | 2026-09-11 08:15:00 | 2444 |
| `ppt_generator/` | 生成ppt | PPT 生成助手 | `3f6dffdac7534bdd87a49734b83d320e` | `990265dac1584de6ac82bf63b0045bfc` | admin | 2026-09-15 01:22:31 | 607 |
| `llm_intel_station/` | 海河天津站 | 大模型情报站（qtt） | `8f9c5471974e415bab1b620f20dc87e9` | `0edd93ce4ea84b978b29021d74a7484a` | admin | 2026-09-16 03:26:06 | 588 |
| `legal_doc_ingestor/` | 法律法规 | 法律法规数据入库专员 | `236165916eba44ae8e7f01b3701cbfdf` | `3811da80cc7e44a39c412fb59a4fa72d` | hyq | 2026-09-23 06:40:07 | 2902 |
