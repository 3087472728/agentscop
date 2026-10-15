#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 脚本名: set_session_permission.py
# 用途:   批量调整会话的权限模式（state.permission_context.mode）。
#
#         平台权限模式是会话级的，只能逐个会话设置。本脚本直接改写
#         sessions.payload，用于把存量会话统一成某个模式（例如全部
#         免确认 bypass，避免智能体执行脚本时反复弹确认、确认卡片
#         丢失后会话卡死）。
#
# 用法:
#   python scripts/set_session_permission.py --list                    # 查看分布
#   python scripts/set_session_permission.py --mode bypass             # 全部改为 bypass
#   python scripts/set_session_permission.py --mode bypass --user hyq  # 仅某用户
#   python scripts/set_session_permission.py --mode accept_edits --agent a129fc97
#
# 模式说明:
#   default      每次操作都确认（最严格）
#   accept_edits 工作区内免确认，工作区外仍询问
#   bypass       所有工具调用一律免确认
# ============================================================
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "agentscope.db")
VALID_MODES = ("default", "accept_edits", "bypass")


def get_mode(payload: str):
    """从会话 payload 中读出当前权限模式。"""
    try:
        p = json.loads(payload)
    except Exception:
        return None, None
    ctx = (p.get("state") or {}).get("permission_context") or {}
    return ctx.get("mode"), p


def main() -> int:
    ap = argparse.ArgumentParser(description="批量调整会话权限模式")
    ap.add_argument("--mode", choices=VALID_MODES, help="要套用的权限模式")
    ap.add_argument("--user", help="仅处理该用户的会话")
    ap.add_argument("--agent", help="仅处理该智能体（id 前缀匹配）的会话")
    ap.add_argument("--list", action="store_true", help="只统计当前分布，不改动")
    args = ap.parse_args()

    if not args.list and not args.mode:
        ap.error("需要指定 --mode，或使用 --list 查看分布")

    con = sqlite3.connect(DB_PATH, timeout=30)
    cur = con.cursor()

    sql = "select id, user_id, agent_id, payload from sessions"
    where, params = [], []
    if args.user:
        where.append("user_id=?")
        params.append(args.user)
    if args.agent:
        where.append("agent_id like ?")
        params.append(args.agent + "%")
    if where:
        sql += " where " + " and ".join(where)

    rows = cur.execute(sql, params).fetchall()

    dist = Counter()
    targets = []
    for sid, uid, aid, payload in rows:
        mode, parsed = get_mode(payload)
        dist[mode] += 1
        if args.list or parsed is None:
            continue
        if mode != args.mode:
            targets.append((sid, uid, aid, parsed, mode))

    print(f"匹配会话 {len(rows)} 个，当前模式分布: {dict(dist)}")

    if args.list:
        con.close()
        return 0

    if not targets:
        print(f"无需调整：所有会话已是 {args.mode}")
        con.close()
        return 0

    print(f"待调整 {len(targets)} 个 -> {args.mode}")
    for sid, uid, aid, parsed, old in targets:
        ctx = parsed["state"].setdefault("permission_context", {})
        ctx["mode"] = args.mode
        cur.execute(
            "update sessions set payload=? where id=?",
            (json.dumps(parsed, ensure_ascii=False), sid),
        )
        print(f"  {sid[:8]} user={uid} agent={aid[:8]} {old} -> {args.mode}")
    con.commit()
    con.close()
    print(f"完成，共更新 {len(targets)} 个会话")
    return 0


if __name__ == "__main__":
    sys.exit(main())
