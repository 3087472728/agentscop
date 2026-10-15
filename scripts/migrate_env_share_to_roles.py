# -*- coding: utf-8 -*-
"""一次性迁移脚本：旧式 .env 智能体共享规则 → roles.json 的 user_agents。

迁移后的 RoleBasedAccessPolicy（server/role_access_policy.py）取代了
OwnedAgentsSharePolicy，因此下列旧式 .env 键不再生效：

- AGENTS_OWNER=admin                （此处仅作信息参考）
- AGENTS_DEFAULT_SHARE=个人助手      → 对所有非 admin 用户可见
- AGENTS_AGENT_SHARE=周报助手:u2;数据质检员:u3  → 按用户显式授权

本脚本把它们转换成 ``config/roles.json`` 里的 ``user_agents``
白名单（键为智能体 **id**，不是名称）：

    { "user_agents": { "u2": ["<周报助手的agent_id>", ...], ... } }

请在后端启动**之后**运行（脚本会调用 GET /agent/ 把智能体名称
映射为 id，并以 admin 身份用 .env 里的密码完成认证）：

    python scripts/migrate_env_share_to_roles.py

幂等性：已存在的 user_agents 条目只合并不删除。当没有剩余可转换
内容（.env 中无旧式键）时，静默退出码 0。

注意：
- 认证使用 .env 中的 AGENTS_USERS（明文，即引导时写入 roles.json
  的同一密码）。若迁移后 admin 密码已轮换，请相应地导出
  AGENTSCOPE_API/AGENTSCOPE_USER，或先运行本脚本再轮换密码。
- 合并语义：名称→id 查找失败的智能体会被报告并跳过；
  用户白名单中已有的智能体不会重复添加。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    EXTRA_ROOT,
    api,
    load_env,
)


def parse_share_rules(raw: str) -> dict[str, list[str]]:
    """把 ``名称:用户1,用户2;名称2:用户3`` 解析成 {name: [users]}。

    common.parse_share_rules 已随旧共享模型一并删除；本脚本作为
    旧规则的一次性迁移工具，自带解析、不再污染公共层。
    """
    rules: dict[str, list[str]] = {}
    for rule in raw.split(";"):
        rule = rule.strip()
        if not rule or ":" not in rule:
            continue
        name, _, users = rule.partition(":")
        name = name.strip()
        viewers = [u.strip() for u in users.split(",") if u.strip()]
        if name and viewers:
            rules.setdefault(name, []).extend(viewers)
    return rules


ROLES_FILE = EXTRA_ROOT / "config" / "roles.json"


def read_roles() -> dict:
    if not ROLES_FILE.is_file():
        print(f"[!] {ROLES_FILE} not found — start the backend once first "
              f"(it bootstraps the file from .env), then re-run.")
        sys.exit(1)
    return json.loads(ROLES_FILE.read_text(encoding="utf-8"))


def write_roles(roles: dict) -> None:
    tmp = str(ROLES_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(roles, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ROLES_FILE)


def main() -> None:
    env = load_env()
    default_share = env.get("AGENTS_DEFAULT_SHARE", "").strip()
    agent_share = parse_share_rules(env.get("AGENTS_AGENT_SHARE", ""))
    if not default_share and not agent_share:
        print("[i] No AGENTS_DEFAULT_SHARE / AGENTS_AGENT_SHARE in .env — nothing to convert.")
        return

    roles = read_roles()
    known_users = [u for u in (roles.get("users") or {}) if u != "admin"]

    # 通过运行中的后端建立 智能体名 → id 映射（admin 可见全部智能体）。
    status, resp = api("GET", "/agent/")
    agents = {a["data"]["name"]: a["id"] for a in (resp or {}).get("agents", [])}

    # 从旧式规则整理 名称 → set(用户) 的授权表。
    grants: dict[str, list[str]] = {}
    if default_share:
        grants.setdefault(default_share, []).extend(known_users)
    for name, users in agent_share.items():
        grants.setdefault(name, []).extend(users)

    user_agents: dict[str, list[str]] = dict(roles.get("user_agents") or {})
    applied, skipped = [], []
    for name, users in grants.items():
        agent_id = agents.get(name)
        if not agent_id:
            skipped.append(name)
            continue
        applied.append(name)
        for user in dict.fromkeys(users):  # 去重，保持顺序
            if user == "admin":
                continue  # admin 始终全量可见
            allow = user_agents.setdefault(user, [])
            if agent_id not in allow:
                allow.append(agent_id)

    if not applied:
        print(f"[!] No agents matched. Known agents: {list(agents)}; "
              f"skipped: {skipped}")
        sys.exit(1)

    roles["user_agents"] = user_agents
    write_roles(roles)

    print(f"[ok] Converted: {applied}")
    if skipped:
        print(f"[!] Not found on the server (skipped): {skipped}")
    print("[ok] roles.json updated — new rules take effect within ~5s.")
    print("[i] You may now remove AGENTS_OWNER / AGENTS_DEFAULT_SHARE / "
          "AGENTS_AGENT_SHARE from .env (they are no longer read).")


if __name__ == "__main__":
    main()
