# -*- coding: utf-8 -*-
"""make_dl_link.py —— 为工作区文件生成聊天可用的下载链接。

迁移自服务器 /home/agentrunner/agentscop/make-dl-link.sh（未提交修改，
2026-11-12），改为跨平台 Python 实现，行为对齐：

1. 优先签发**下载短链**（POST /workspace/files/short-link → /dl/<code>），
   并跟随 302 实际下载一次回验全链路；短链服务不可用时回退旧长链
   （download-token）。
2. path 归一：智能体常把带 ``users/<owner>/`` 前缀的完整工作区相对
   路径原样传入，而下载接口的 path 必须相对会话工作区根
   （``workspaces/<agent_id>/users/<user_id>/``），因此先剥前缀。
3. path 定位顺序：会话工作区根 → 智能体工作区根（旧布局）→
   自动补技能前缀 ``skills/<agent_id>/``（两处各试一次）。
4. 鉴权用户依次尝试：会话属主 → admin → qtt → agentrunner。

用法（在本项目根目录）：
    python scripts/make_dl_link.py --path outputs/report.docx \\
        --session <session_id> [--agent <agent_id>] [--owner <user>]

    --backend   后端地址（默认读 AGENTS_BACKEND_URL，再退
                http://127.0.0.1:8020；容器部署传网关地址）
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- 参数
p = argparse.ArgumentParser(description="生成工作区文件下载链接（优先短链）")
p.add_argument("--path", required=True, help="工作区相对路径（可带 users/<user>/ 前缀）")
p.add_argument("--session", required=True, help="会话 ID")
p.add_argument("--agent", default=None, help="智能体 ID（缺省时从本地库按会话解析）")
p.add_argument("--owner", default=None, help="会话属主用户 ID（缺省时从本地库解析）")
p.add_argument(
    "--backend",
    default=os.environ.get("AGENTS_BACKEND_URL", "http://127.0.0.1:8020"),
    help="后端基地址",
)
p.add_argument(
    "--users",
    default="admin,qtt,agentrunner",
    help="鉴权候选用户列表（逗号分隔，会话属主优先）",
)
args = p.parse_args()

BACKEND = args.backend.rstrip("/")

# ------------------------------------------------- 从本地库解析会话信息
def resolve_session(session_id: str) -> tuple[str, str]:
    """从 data/agentscope.db 查 (agent_id, owner)；查不到返回空串。"""
    db_path = os.environ.get(
        "AGENTS_DB_PATH",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "agentscope.db"),
    )
    if not os.path.isfile(db_path):
        return "", ""
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT agent_id, COALESCE(owner, user_id, '') FROM sessions "
            "WHERE id=? OR session_id=? LIMIT 1",
            (session_id, session_id),
        ).fetchone()
        con.close()
        if row:
            return str(row[0] or ""), str(row[1] or "")
    except sqlite3.Error:
        pass
    return "", ""


AGENT_ID = args.agent or resolve_session(args.session)[0]
OWNER = args.owner or resolve_session(args.session)[1]
if not AGENT_ID:
    print("错误: 无法确定 agent_id，请用 --agent 显式指定", file=sys.stderr)
    sys.exit(1)

REL_PATH = args.path

# --------------------------------------------------------- path 归一
# 剥掉误带的 users/<owner>/ 前缀（下载接口要求相对会话工作区根）
if OWNER:
    prefix = f"users/{OWNER}/"
    if REL_PATH.startswith(prefix):
        REL_PATH = REL_PATH[len(prefix):]

# ------------------------------------------------------------ 请求工具
def http_json(method: str, url: str, params: dict, user: str):
    """带 X-User-Id 头发请求，返回解析后的 JSON（失败返回 None）。"""
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", method=method)
    req.add_header("X-User-Id", user)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


CANDIDATES = [u for u in [OWNER] if u] + [u.strip() for u in args.users.split(",") if u.strip()]

# ------------------------------------------- 首选：下载短链（/dl/<code>）
SHORT_URL = ""
for user in CANDIDATES:
    resp = http_json(
        "POST",
        f"{BACKEND}/workspace/files/short-link",
        {"agent_id": AGENT_ID, "session_id": args.session, "path": REL_PATH},
        user,
    )
    if resp and resp.get("url"):
        SHORT_URL = resp["url"]
        break

if SHORT_URL:
    # 回验：跟随 302 实际下载一次，确保 短码→令牌→文件 全链路可用
    # （urllib 默认 redirect handler 会自动跟随 302）
    try:
        status = urllib.request.urlopen(urllib.request.Request(SHORT_URL), timeout=20).status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        status = 0
    if status != 200:
        print(
            f"警告: 短链自检未通过 (HTTP {status})，请检查 path 是否相对会话工作区根",
            file=sys.stderr,
        )
    print(f"[📥 点击下载]({SHORT_URL})")
    sys.exit(0)

# ------------------------------------- 回退：旧长链（短链服务未启用时）
TOKEN = ""
for user in CANDIDATES:
    resp = http_json(
        "POST",
        f"{BACKEND}/workspace/files/download-token",
        {"agent_id": AGENT_ID, "session_id": args.session, "path": REL_PATH},
        user,
    )
    if resp and resp.get("token"):
        TOKEN = resp["token"]
        break

if not TOKEN:
    print("错误: 短链与令牌均签发失败（后端未启动或会话不可解析）", file=sys.stderr)
    sys.exit(1)

ENC = urllib.parse.quote(REL_PATH, safe="")
# 回验：用签发的 token 实际请求一次下载，确保 path 与会话工作区根匹配
try:
    req = urllib.request.Request(
        f"{BACKEND}/workspace/files?agent_id={urllib.parse.quote(AGENT_ID)}"
        f"&session_id={urllib.parse.quote(args.session)}"
        f"&path={ENC}&download=true&token={TOKEN}"
    )
    status = urllib.request.urlopen(req, timeout=15).status
except urllib.error.HTTPError as e:
    status = e.code
except Exception:
    status = 0
if status != 200:
    print(f"警告: 长链自检未通过 (HTTP {status})", file=sys.stderr)

print(f"[📥 点击下载]({BACKEND}/workspace/files?agent_id={urllib.parse.quote(AGENT_ID)}&session_id={urllib.parse.quote(args.session)}&path={ENC}&download=true&token={TOKEN})")
