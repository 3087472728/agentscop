# -*- coding: utf-8 -*-
# ============================================================
# 模块名: skill_visibility.py
# 用途:   技能库按角色可见（admin 全部 / leader 同团队 / member 仅自己）——
#         monkey-patch 存储层的 list_skills：在用户自己的技能之外，
#         按角色合并其他用户安装的技能（admin 额外查库取全部用户）
# 接口:   setup(storage) —— 替换 type(storage).list_skills，启动时调用一次
# 依赖:   roles.json（角色/团队配置）+ agentscope.db（skills 表）
# ============================================================
import json
import os
import sqlite3

from . import paths

_RA_ROLES_FILE = os.path.join(paths.CONFIG_DIR, "roles.json")


def _ra_visible_owners(user_id: str) -> list[str] | None:
    """返回其他可见技能的 user_id 列表；None 表示全部可见（admin）。"""
    try:
        with open(_RA_ROLES_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return []
    info = (cfg.get("users") or {}).get(user_id) or {}
    role = info.get("role", "member")
    if role == "admin":
        return None
    if role == "leader":
        team = info.get("team")
        return [
            uid
            for uid, u in (cfg.get("users") or {}).items()
            if uid != user_id and u.get("team") == team
        ]
    return []


def setup(storage) -> None:
    """替换存储层的 list_skills，注入技能按角色可见逻辑。"""

    async def _ra_list_skills(self, user_id: str):
        own = await _ra_orig_list_skills(self, user_id)
        owners = _ra_visible_owners(user_id)
        if owners is None:
            con = sqlite3.connect(
                f"file:{paths.DB_PATH}?mode=ro", uri=True
            )
            try:
                owners = [
                    r[0]
                    for r in con.execute("SELECT DISTINCT user_id FROM skills")
                    if r[0] != user_id
                ]
            finally:
                con.close()
        merged = list(own)
        seen = {s.name for s in merged}
        for owner in owners or []:
            for rec in await _ra_orig_list_skills(self, owner):
                if rec.name not in seen:
                    merged.append(rec)
                    seen.add(rec.name)
        return merged

    _ra_orig_list_skills = type(storage).list_skills
    type(storage).list_skills = _ra_list_skills
