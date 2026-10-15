# -*- coding: utf-8 -*-
"""一次性清理「幻影资源」：owner 已不在 roles.json 的存量 Redis 资源。

背景：历史版本删除用户时不做资源处置，其 agent / 知识库 / 凭证
残留在 Redis 里——所有人不可见、管理页清不掉（幻影资源）。本脚本
扫描这类孤儿用户的数据，列出清单确认后批量转移给 admin（转入
「归属即规则」：agent 按 user_agents 白名单可见、KB/凭证全员只读）。

扫描方式：SCAN ``agentscope:user:*`` 键空间，解析出全部出现过的
user_id，与 roles.json 的 users 求差集即孤儿名单；转移只针对
可见性相关的 agent / 知识库 / 凭证三类（经 storage 公开 API，
逐条 upsert 到 admin 名下后删除旧记录，agent 的会话随
delete_agent 级联清理）。

用法：
    python reclaim_orphan_resources.py            # 仅扫描，列出孤儿清单
    python reclaim_orphan_resources.py --apply    # 扫描并批量转移给 admin

依赖：直连 RedisStorage（不经 HTTP），环境变量与 server/main.py 相同
（AGENTS_REDIS_HOST/PORT 或 REDIS_HOST/PORT、REDIS_PASSWORD）。
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROLES_FILE  # noqa: E402


def build_storage():
    """构造 RedisStorage（环境变量取法与 server/main.py 一致）。"""
    host = os.getenv("AGENTS_REDIS_HOST") or os.getenv("REDIS_HOST") or "127.0.0.1"
    port = int(os.getenv("AGENTS_REDIS_PORT") or os.getenv("REDIS_PORT") or "6379")
    from agentscope.app.storage import RedisStorage

    return RedisStorage(
        host=host,
        port=port,
        password=os.getenv("REDIS_PASSWORD") or None,
    )


def scan_user_ids(storage) -> set[str]:
    """SCAN 键空间，收集出现过的全部 user_id。

    所有 per-user 键都以 ``agentscope:user:{uid}:`` 开头（另有少量
    全局键 ``agentscope:schedules`` 等，不含 user 段，天然不匹配）。
    """
    found: set[str] = set()
    client = storage.get_client()
    for key in client.scan_iter(match="agentscope:user:*", count=500):
        parts = str(key).split(":")
        # agentscope : user : {uid} : ...
        if len(parts) >= 3 and parts[2]:
            found.add(parts[2])
    return found


async def transfer_all_to_admin(storage, orphan: str) -> dict[str, int]:
    """把孤儿用户名下 agent/KB/凭证 转移给 admin（与 409 决策流同语义）。"""
    counts: Counter = Counter()

    for rec in await storage.list_credentials(orphan):
        rec.user_id = "admin"
        await storage.upsert_credential("admin", rec)
        await storage.delete_credential(orphan, rec.id)
        counts["credentials"] += 1

    for rec in await storage.list_knowledge_bases(orphan):
        kb_id = rec.id
        try:
            docs = await storage.list_knowledge_documents(orphan, kb_id)
        except Exception:
            docs = []
        rec.user_id = "admin"
        await storage.upsert_knowledge_base("admin", rec)
        for doc in docs:
            doc.user_id = "admin"
            await storage.upsert_knowledge_document("admin", kb_id, doc)
            await storage.delete_knowledge_document(orphan, kb_id, doc.id)
        await storage.delete_knowledge_base(orphan, kb_id)
        counts["knowledge_bases"] += 1

    for rec in await storage.list_agents(orphan):
        rec.user_id = "admin"
        await storage.upsert_agent("admin", rec)
        await storage.delete_agent(orphan, rec.id)  # 级联清理其会话/定时任务
        counts["agents"] += 1

    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="实际转移（默认只扫描列出清单）"
    )
    args = parser.parse_args()

    if os.getenv("AGENTSCOPE_REDIS_URL"):
        print("note: AGENTSCOPE_REDIS_URL is not used; host/port envs only.")

    roles = json.loads(ROLES_FILE.read_text(encoding="utf-8"))
    known = set((roles.get("users") or {}).keys())

    storage = build_storage()

    async def run() -> int:
        async with storage:
            orphans = scan_user_ids(storage) - known
            if not orphans:
                print("no orphan owners found — Redis 与 roles.json 一致。")
                return 0
            print(f"orphan owners ({len(orphans)}): {sorted(orphans)}")
            total: Counter = Counter()
            for uid in sorted(orphans):
                inv = {
                    kind: len(await getattr(storage, lister)(uid))
                    for kind, lister in (
                        ("agents", "list_agents"),
                        ("knowledge_bases", "list_knowledge_bases"),
                        ("credentials", "list_credentials"),
                    )
                }
                print(f"  {uid}: {inv}")
                if not args.apply:
                    continue
                moved = await transfer_all_to_admin(storage, uid)
                total.update(moved)
                print(f"    -> transferred to admin: {moved}")
            if args.apply:
                print(f"\nDONE. total transferred: {dict(total)}")
            else:
                print("\n(dry-run) add --apply to transfer all to admin.")
            return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
