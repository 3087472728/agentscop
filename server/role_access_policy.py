# -*- coding: utf-8 -*-
# ============================================================
# 脚本名: role_access_policy.py
# 用途:   资源可见性策略（注入 agentscope create_app）
#         「归属即规则」，无需逐资源配置 ACL：
#
#         - admin 名下资源 = 系统/公共资源：
#             · 知识库、模型凭证 → 全员只读可见
#             · 智能体           → 仅 user_agents 白名单内的用户可见
#         - 用户自建资源 = 私有资源：
#             · 仅归属者自己 + admin 可见（自己可改，admin 可改）
#         - admin：全量可见，且可编辑他人资源（admin_edit）
#         - 归属者本人对其资源的权限由框架默认逻辑保证，
#           本策略只负责「别人的资源」
#
# 配置:   roles_store（Redis roles:* 键族；AGENTS_ROLES_STORE=file 时为
#         ../config/roles.json）：
#         {
#           "admin_edit": true,
#           "users":   { "<id>": {"name": "...", "role": "admin|leader|member", "team": "..."} },
#           "user_agents": { "<id>": ["<agent_id>", ...] }   # 可用智能体白名单
#         }
#         热读取经 store 版本号轮询缓存（每请求 1 次 GET roles:ver），
#         改完无需重启
#
# 说明:   - team 字段目前仅作组织记录；leader 同团队可见性已按需求
#           暂时关闭，未来启用时扩展 _team_visible() 即可
#         - 旧版 owner 级共享键（kb_shared_owners / agent_shared_owners /
#           credential_shared）与资源级 ACL 键（*_acls）已废弃，
#           若存在于 roles.json 中将被忽略
# 迁移注: 源自 agent-new/server/role_access_policy.py，适配点：
#         ① 导入路径改为 agentscope.app.access（目标框架版本）
#         ② _all_user_ids() 原 SQL 查询 SQLite（源项目存储），
#           目标项目为 RedisStorage，改为读用户表的 users 键
#         ③ 构造函数不再需要 db_path
#         ④ 第九章改造：配置读取走 roles_store（Redis 现役 / 文件回滚），
#           users 键为不可变用户 id；viewer_id 亦为 id——匹配逻辑不变
# ============================================================
"""资源可见性策略：归属即规则，无需逐资源配置 ACL。"""

from __future__ import annotations

from typing import Any

from agentscope.app.access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)


async def _load_roles() -> dict[str, Any]:
    """读取角色配置（store 版本号轮询缓存；降级时回落内存/快照）。"""
    from .roles_store import get_store

    return await get_store().load_all()


class RoleBasedAccessPolicy(ResourceAccessPolicyBase):
    """admin 名下=公共资源（按种类定可见性），用户名下=私有资源。"""

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    @staticmethod
    def _role_of(viewer_id: str, cfg: dict[str, Any]) -> tuple[str, str | None]:
        info = (cfg.get("users") or {}).get(viewer_id) or {}
        return info.get("role", "member"), info.get("team")

    @staticmethod
    def _team_visible(
        viewer_id: str,
        owner_id: str,
        kind: ResourceKind,
        cfg: dict[str, Any],
    ) -> bool:
        """同团队可见性开关。

        需求现状：团队规则未定，暂时一律不可见。
        未来启用时在此实现（例如 leader 可见同团队 member 的私有资源），
        返回值需与 list_accessible 中的过滤逻辑联动。
        """
        return False

    # ------------------------------------------------------------------
    # 判定链（唯一，无覆盖关系）：
    #   ① viewer == owner       → 框架默认逻辑保证，本策略不处理
    #   ② viewer == admin       → 其他所有用户的资源全量可见（按 admin_edit 定读写）
    #   ③ owner == admin        → 公共资源：KB/凭证全员只读；智能体按白名单
    #   ④ 其他（用户私有资源）  → 默认不可见（团队可见性暂关闭）
    # ------------------------------------------------------------------
    _LISTERS = {
        ResourceKind.AGENT: "list_agents",
        ResourceKind.CREDENTIAL: "list_credentials",
        ResourceKind.KNOWLEDGE_BASE: "list_knowledge_bases",
    }

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: Any,
    ) -> list[ResourceRef]:
        cfg = await _load_roles()
        role, _team = self._role_of(viewer_id, cfg)

        # ── admin：全量可见其他用户的资源 ────────────────────────
        if role == "admin":
            owners = [
                u for u in (cfg.get("users") or {}) if u != viewer_id
            ]
            allowed_ids: set[str] | None = None  # 不过滤
            permission = (
                ResourcePermission.EDIT
                if cfg.get("admin_edit", True)
                else ResourcePermission.READ
            )
        else:
            # ── 普通用户：仅可见 admin 名下的公共资源 ────────────
            owners = ["admin"] if "admin" != viewer_id else []
            permission = ResourcePermission.READ

            # 智能体是白名单严格模式：勾选了才能看到（键不存在=未勾选任何）
            allowed_ids = None
            if kind == ResourceKind.AGENT:
                allowlist = (cfg.get("user_agents") or {}).get(viewer_id) or []
                allowed_ids = set(allowlist)

        lister = getattr(storage, self._LISTERS[kind])
        refs: list[ResourceRef] = []
        for owner in owners:
            # 团队可见性扩展位仅约束普通用户；admin 判定链②全量可见，
            # 不受团队开关影响
            if (
                role != "admin"
                and owner != "admin"
                and not self._team_visible(viewer_id, owner, kind, cfg)
            ):
                continue
            try:
                records = await lister(owner)
            except Exception:
                continue
            for rec in records:
                if allowed_ids is not None and rec.id not in allowed_ids:
                    continue
                refs.append(
                    ResourceRef(
                        kind=kind,
                        owner_id=owner,
                        resource_id=rec.id,
                        permission=permission,
                    )
                )
        return refs
