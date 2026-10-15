# -*- coding: utf-8 -*-
"""注册/更新智能体（属主为 admin，归属即规则的公共属主）。

幂等 upsert：按显示名匹配——已存在则 PATCH 更新，不存在则 POST
创建。写入 agentscope_extra/registry.json，记录 名称 → agent_id
的映射。

智能体为单一属主制：全部注册在 admin 名下；普通用户经管理页
维护的 config/roles.json ``user_agents`` 白名单获得只读访问。
旧的 .env AGENTS_OWNER / AGENTS_DEFAULT_SHARE / AGENTS_AGENT_SHARE
已废弃。

用法：
    python register_agents.py
"""
import json

from common import AGENTS_DIR, api, load_registry, save_registry


def load_agent_cfg(agent_dir) -> dict:
    cfg = json.loads((agent_dir / "agent.json").read_text(encoding="utf-8"))
    prompt_file = cfg.pop("system_prompt_file", None)
    if prompt_file:
        cfg["system_prompt"] = (agent_dir / prompt_file).read_text(encoding="utf-8")
    if not cfg.get("system_prompt"):
        cfg["system_prompt"] = ""
    return cfg


def main() -> int:
    registry = load_registry()
    if not AGENTS_DIR.is_dir():
        print(f"No agents directory at {AGENTS_DIR}")
        return 1

    _, listed = api("GET", "/agent/")
    by_name = {view["data"]["name"]: view for view in listed["agents"]}

    changed = 0
    for agent_dir in sorted(p for p in AGENTS_DIR.iterdir() if p.is_dir()):
        cfg = load_agent_cfg(agent_dir)
        name = cfg["name"]
        existing = by_name.get(name)
        if existing:
            api(
                "PATCH",
                f"/agent/{existing['id']}",
                {"name": name, "system_prompt": cfg["system_prompt"]},
            )
            entry = {"agent_id": existing["id"], "action": "updated"}
            print(f"[updated] {name} -> {existing['id']}")
        else:
            _, created = api("POST", "/agent/", cfg)
            entry = {"agent_id": created["agent_id"], "action": "created"}
            print(f"[created] {name} -> {created['agent_id']}")
        previous = dict(registry.get(name, {}))
        previous.update(entry)
        registry[name] = previous
        changed += 1

    save_registry(registry)
    print(f"\nDone. {changed} agent(s) synced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
