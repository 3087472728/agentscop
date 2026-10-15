# -*- coding: utf-8 -*-
# ============================================================
# 模块名: kb_credential_fallback_patch.py
# 用途:   知识库凭证解析回退补丁——修复跨用户凭证引用 404。
#
#         框架 CollectionPerKbManager.get_knowledge() 按
#         (KB owner, credential_id) 直查凭证（owner-internal
#         path，见 _collection_per_kb.py），不走
#         ResourceAccessPolicy 的共享语义。当 KB 引用了别人
#         名下的凭证（通过 credential_acls / credential_shared
#         共享）时查不到 → KnowledgeBaseNotFoundError →
#         索引/检索/聊天引用失败。
#
#         本补丁给 KB manager 的 storage 包一层代理：
#         owner 名下查不到凭证时，按 credential_id 直查 DB
#         找到真实 owner（cid 全局唯一），再回该 owner 名下
#         取凭证。覆盖 ACL 共享 / owner 级共享 / 任何跨用户
#         引用场景；KB 能引用到该凭证，说明配置时已通过
#         API 层的可见性校验。
#
#         索引 worker / KB 检索 / 聊天引用三条路径都经由
#         app.state.knowledge_base_manager，因此只需在该实例
#         的 _storage 上安装代理。
# 接口:   setup(app) —— 幂等安装
# ============================================================
import logging
import os
import sqlite3

logger = logging.getLogger("main.kb_credential_fallback")

from . import paths

DB_PATH = paths.DB_PATH


def _owner_of_credential(credential_id: str) -> str | None:
    """按全局唯一的 credential_id 直查 DB，返回真实 owner。

    Redis 存储模式下没有 SQLite 文件可查（服务器部署本就没有此
    补丁），返回 None 让代理退化为直通行为，不影响正常读写。
    """
    if paths.STORAGE_MODE == "redis":
        return None
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        con.execute("PRAGMA busy_timeout=3000")
        row = con.execute(
            "SELECT user_id FROM credentials WHERE id = ?", (credential_id,)
        ).fetchone()
        return row[0] if row and isinstance(row[0], str) else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


class _SharedCredentialStorageProxy:
    """storage 代理：get_credential 未命中时按 cid 回退查找真实 owner。

    其余属性/方法原样委托给被包装的 storage 实例。
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def get_credential(self, user_id: str, credential_id: str):
        record = await self._inner.get_credential(user_id, credential_id)
        if record is not None:
            return record

        # 回退：按 cid 直查真实 owner（cid 全局唯一）
        owner = _owner_of_credential(credential_id)
        if owner and owner != user_id:
            record = await self._inner.get_credential(owner, credential_id)
            if record is not None:
                logger.info(
                    "凭证 %s 在 %s 名下未找到，已回退到真实 owner %s",
                    credential_id[:8],
                    user_id,
                    owner,
                )
                return record
        return None


def setup(app) -> None:
    """给 knowledge_base_manager 安装共享凭证回退代理（幂等）。"""
    manager = getattr(app.state, "knowledge_base_manager", None)
    if manager is None:
        logger.warning("app.state 无 knowledge_base_manager，跳过补丁")
        return
    if getattr(manager, "_storage", None) is None:
        logger.warning("knowledge_base_manager 无 _storage，跳过补丁")
        return
    if isinstance(manager._storage, _SharedCredentialStorageProxy):
        return
    manager._storage = _SharedCredentialStorageProxy(manager._storage)
    logger.info("已启用知识库共享凭证回退（按 credential_id 解析真实 owner）")
