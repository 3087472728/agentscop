# -*- coding: utf-8 -*-
"""管理员一键配置：全局模型收敛到智谱 Coding Plan。

对 roles.json 中的每个用户（或 --user 指定的用户），以
X-On-Behalf-Of 代操作身份执行：
- 确保存在 zhipu_coding_plan 类型凭据（读 .env 的 ZAI_API_KEY/ZAI_BASE_URL）
- 删除该用户名下旧的 openai_credential 凭据（模型选择器中的 GPT 目录随之消失）
- 修复该用户全部存量会话的 chat_model_config 为 glm-5.3-flash
  （会话可能落在自己名下的 agent 或授权给该用户的 agent 上）

Usage:
    python configure_model.py                 # roles.json 的所有用户
    python configure_model.py --user u2      # 仅处理 u2
"""
import argparse

from common import agents_users, api, load_env, load_registry


def ensure_plan_credential(user: str) -> str:
    env = load_env()
    api_key = env.get("ZAI_API_KEY")
    base_url = env.get("ZAI_BASE_URL")
    if not api_key or not base_url:
        raise SystemExit("ZAI_API_KEY / ZAI_BASE_URL missing in .env")

    _, listed = api("GET", "/credential/", user=user)
    for view in listed["credentials"]:
        data = view.get("data") or {}
        if (
            data.get("type") == "zhipu_coding_plan"
            and data.get("base_url") == base_url
        ):
            print(f"  credential: reuse {view['id']}")
            return view["id"]

    _, created = api(
        "POST",
        "/credential/",
        {
            "data": {
                "type": "zhipu_coding_plan",
                "name": "Zhipu Coding Plan",
                "api_key": api_key,
                "base_url": base_url,
            }
        },
        user=user,
    )
    print(f"  credential: created {created['credential_id']}")
    return created["credential_id"]


def remove_legacy_openai_credentials(user: str) -> int:
    _, listed = api("GET", "/credential/", user=user)
    removed = 0
    for view in listed["credentials"]:
        data = view.get("data") or {}
        if data.get("type") == "openai_credential":
            api("DELETE", f"/credential/{view['id']}", user=user)
            print(f"  credential: removed legacy openai {view['id']}")
            removed += 1
    return removed


def fix_sessions(
    user: str, registry: dict, credential_id: str, model: str
) -> tuple[int, int]:
    desired = {
        "type": "zhipu_coding_plan",
        "credential_id": credential_id,
        "model": model,
        "parameters": {},
    }
    fixed = 0
    scanned = 0
    for display, info in sorted(registry.items()):
        agent_id = info["agent_id"]
        try:
            _, listed = api("GET", f"/sessions/?agent_id={agent_id}", user=user)
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                # Agent not visible to this user (not shared) — its
                # sessions cannot belong to them either.
                continue
            raise
        views = listed.get("sessions", [])
        for view in views:
            record = view.get("session", view)
            session_id = record["id"]
            scanned += 1
            current = (record.get("config") or {}).get("chat_model_config")
            if current == desired:
                continue
            api(
                "PATCH",
                f"/sessions/{session_id}?agent_id={agent_id}",
                {"chat_model_config": desired},
                user=user,
            )
            fixed += 1
            print(
                f"  fixed {display}/{session_id}: "
                f"{(current or {}).get('model', '<none>')} -> {model}"
            )
    return scanned, fixed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=None, help="Only process this user")
    args = parser.parse_args()

    env = load_env()
    model = env.get("ZAI_MODEL", "glm-5.3-flash")

    registry = load_registry()
    if not registry:
        print("registry.json empty — run register_agents.py first.")
        return 1

    users = sorted(agents_users().keys())
    if args.user:
        users = [u for u in users if u == args.user]
    if not users:
        print(f"User '{args.user}' not found in roles.json.")
        return 1

    for user in users:
        print(f"=== user '{user}' ===")
        credential_id = ensure_plan_credential(user)
        removed = remove_legacy_openai_credentials(user)
        print(f"  legacy openai credentials removed: {removed}")
        scanned, fixed = fix_sessions(user, registry, credential_id, model)
        print(f"  sessions scanned={scanned}, fixed={fixed}")

    print(f"\nDone. model={model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
