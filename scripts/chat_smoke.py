# -*- coding: utf-8 -*-
"""LLM 冒烟测试：对可见智能体执行完整对话往返。

确保存在凭证（来自 agentscope_extra/.env 的 zhipu_coding_plan）、
配置会话的对话模型、触发一轮对话，并轮询消息直到助手回复。
智能体按显示名在调用者的可见范围内解析（自有和共享的都行）。

用法：
    python chat_smoke.py --name 周报助手 --message "列出你的技能" [--user u2]
"""
import argparse
import json
import time

from common import api, load_env

MSG_ENDPOINT_TIMEOUT = 180.0
POLL_INTERVAL = 3.0


def resolve_agent(user: str, name: str) -> str:
    """在 ``user`` 的可见范围内按显示名解析智能体 id。"""
    _, listed = api("GET", "/agent/", user=user)
    for view in listed["agents"]:
        if view["data"]["name"] == name:
            return view["id"]
    raise SystemExit(f"Agent '{name}' is not visible to user '{user}'.")


def ensure_credential(user: str | None = None) -> str:
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
            print(f"credential: reuse {view['id']}")
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
    print(f"credential: created {created['credential_id']}")
    return created["credential_id"]


def text_of(msg: dict) -> str:
    parts = []
    for block in msg.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Agent display name (registry key)")
    parser.add_argument("--user", default=None, help="X-User-ID (default: common.USER_ID)")
    parser.add_argument("--message", required=True, help="User message to send")
    args = parser.parse_args()

    import common

    user = args.user or common.USER_ID

    agent_id = resolve_agent(user, args.name)
    print(f"agent: {args.name} -> {agent_id}")

    credential_id = ensure_credential(user)
    env = load_env()
    model = env.get("ZAI_MODEL", "glm-5.3-flash")

    _, session = api(
        "POST",
        "/sessions/",
        {"agent_id": agent_id, "name": f"chat-smoke-{int(time.time())}"},
        user=user,
    )
    session_id = session["session_id"]
    print(f"session: {session_id} (user {user})")

    api(
        "PATCH",
        f"/sessions/{session_id}?agent_id={agent_id}",
        {
            "chat_model_config": {
                "type": "zhipu_coding_plan",
                "credential_id": credential_id,
                "model": model,
                "parameters": {},
            }
        },
        user=user,
    )
    print(f"model: {model}")

    api(
        "POST",
        "/chat/",
        {
            "agent_id": agent_id,
            "session_id": session_id,
            "input": {
                "name": "user",
                "role": "user",
                "content": [{"type": "text", "text": args.message}],
            },
        },
        user=user,
    )
    print(f"chat triggered: {args.message!r}")

    deadline = time.monotonic() + MSG_ENDPOINT_TIMEOUT
    reply = None
    while time.monotonic() < deadline:
        _, messages = api(
            "GET",
            f"/sessions/{session_id}/messages?agent_id={agent_id}",
            timeout=30,
            user=user,
        )
        items = messages["messages"] if isinstance(messages, dict) else messages
        assistants = [m for m in items if m.get("role") == "assistant"]
        if assistants:
            reply = text_of(assistants[-1])
            if reply.strip():
                break
        time.sleep(POLL_INTERVAL)

    if not reply:
        print("TIMEOUT: no assistant reply within", MSG_ENDPOINT_TIMEOUT, "s")
        return 2

    print(f"\n--- assistant reply (session {session_id}) ---\n{reply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
