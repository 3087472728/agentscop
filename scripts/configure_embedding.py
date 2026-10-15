# -*- coding: utf-8 -*-
"""管理员一键配置：知识库本地 embedding 凭据（local_bge / TEI）。

对 AGENTS_USERS 中的每个用户（或 --user 指定的用户）：
- 确保存在 local_bge 类型凭据（读 .env 的 TEI_BASE_URL，默认
  http://localhost:19556/v1；TEI 无鉴权，api_key 写占位符 local）

知识库建库页的 embedding 模型列表按"用户可见凭据"聚合，因此每个
用户都需要自己的 local_bge 凭据才能创建知识库。

Usage:
    python configure_embedding.py                 # AGENTS_USERS 的所有用户
    python configure_embedding.py --user u2      # 仅处理 u2
"""
import argparse

from common import agents_users, api, load_env

DEFAULT_TEI_BASE_URL = "http://localhost:19556/v1"


def ensure_bge_credential(user: str, base_url: str) -> str:
    _, listed = api("GET", "/credential/", user=user)
    for view in listed["credentials"]:
        data = view.get("data") or {}
        if data.get("type") == "local_bge":
            if data.get("base_url") == base_url:
                print(f"  credential: reuse {view['id']}")
                return view["id"]
            # base_url 变更（改端口等）：删旧建新，保持唯一事实源
            api("DELETE", f"/credential/{view['id']}", user=user)
            print(f"  credential: removed stale local_bge {view['id']}")
            break

    _, created = api(
        "POST",
        "/credential/",
        {
            "data": {
                "type": "local_bge",
                "name": "本地 BGE (TEI)",
                "api_key": "local",
                "base_url": base_url,
            }
        },
        user=user,
    )
    print(f"  credential: created {created['credential_id']}")
    return created["credential_id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=None, help="Only process this user")
    args = parser.parse_args()

    base_url = load_env().get("TEI_BASE_URL", DEFAULT_TEI_BASE_URL).rstrip("/")

    users = sorted(agents_users().keys())
    if args.user:
        users = [u for u in users if u == args.user]
    if not users:
        print(f"User '{args.user}' not found in AGENTS_USERS (.env).")
        return 1

    for user in users:
        print(f"=== user '{user}' ===")
        ensure_bge_credential(user, base_url)

    print(f"\nDone. embedding endpoint = {base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
