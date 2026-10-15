# -*- coding: utf-8 -*-
# ============================================================
# 模块名: session_permission_patch.py
# 用途:   让新建会话带上默认权限模式（accept_edits）。
#
#         平台的权限模式是「会话级」的，框架创建会话时不带
#         permission_mode，于是每次都落到最严格的 default（每次
#         操作都要点确认）。这里包装创建会话接口：会话建好后，
#         若仍是默认的 default 模式，就自动切成 accept_edits
#         （工作目录内的文件读写与命令免确认，工作区外仍询问）。
#
#         已有会话的批量调整由 scripts/set_default_permission.py 完成。
# 接口:   setup(app, storage) —— 启动时调用一次
# ============================================================
import json
import logging
import os
import sqlite3

from fastapi import Depends
from fastapi.routing import APIRoute, _IncludedRouter

from agentscope.app._router._schema import CreateSessionRequest
from agentscope.app.deps import (
    get_current_user_id,
    get_resource_access_service,
    get_workspace_manager,
)

from . import paths

logger = logging.getLogger("main.session_permission")

# 新会话默认套用的权限模式：
#   accept_edits —— 工作区内免确认，工作区外仍弹确认
#   bypass       —— 所有工具调用一律免确认（内网自用场景，少打扰）
DEFAULT_PERMISSION_MODE = "bypass"


async def _apply_default_mode(storage, user_id: str, agent_id: str, session_id: str) -> None:
    """把会话的权限模式从 default 切到默认值（不覆盖用户显式设置）。

    SQLite 模式：直接改写会话记录里的 ``state.permission_context.mode``。
    用 SQL 而非 storage.upsert_session，是因为刚创建的空会话没有运行态
    需要保留，直接落库更可靠、也避免误覆盖其他 state 字段。

    Redis 模式：无 SQLite 文件，走通用 storage API（get_session →
    改 AgentState.permission_context → upsert_session）。
    """
    if paths.STORAGE_MODE == "redis":
        try:
            from agentscope.permission import PermissionContext
            from agentscope.state import AgentState

            record = await storage.get_session(user_id, agent_id, session_id)
            if record is None or record.state is None:
                return
            ctx = record.state.permission_context
            if ctx is None or ctx.mode != "default":
                return
            ctx.mode = DEFAULT_PERMISSION_MODE
            await storage.upsert_session(
                user_id,
                agent_id,
                record.config,
                state=record.state,
                session_id=session_id,
            )
            print(
                f"[perm-patch] 会话 {session_id} 权限模式 default -> "
                f"{DEFAULT_PERMISSION_MODE}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 — 失败不影响会话创建
            print(f"[perm-patch] 设置失败({session_id}): {exc}", flush=True)
        return

    db_path = paths.DB_PATH
    try:
        con = sqlite3.connect(db_path, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"[perm-patch] 打开数据库失败: {exc}", flush=True)
        return
    try:
        row = con.execute(
            "select payload from sessions where id=? and user_id=? and agent_id=?",
            (session_id, user_id, agent_id),
        ).fetchone()
        if row is None:
            print(f"[perm-patch] 未找到会话 {session_id}", flush=True)
            return
        p = json.loads(row[0])
        ctx = (p.get("state") or {}).get("permission_context") or {}
        if ctx.get("mode") != "default":
            print(
                f"[perm-patch] 会话 {session_id} 模式为 {ctx.get('mode')}，跳过",
                flush=True,
            )
            return
        ctx["mode"] = DEFAULT_PERMISSION_MODE
        p["state"]["permission_context"] = ctx
        con.execute(
            "update sessions set payload=? where id=?",
            (json.dumps(p, ensure_ascii=False), session_id),
        )
        con.commit()
        print(
            f"[perm-patch] 会话 {session_id} 权限模式 default -> {DEFAULT_PERMISSION_MODE}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 — 失败不影响会话创建
        print(f"[perm-patch] 设置失败({session_id}): {exc}", flush=True)
    finally:
        con.close()


def setup(app, storage) -> None:
    """包装 POST /sessions/：创建后套用默认权限模式。"""
    target_path = "/sessions/"
    replaced = False
    for wrapper in app.router.routes:
        if not isinstance(wrapper, _IncludedRouter):
            continue
        router = wrapper.original_router
        paths = {getattr(r, "path", "") for r in router.routes}
        # 用 '/sessions/{session_id}' 特征路由确认这是 session_router
        if target_path not in paths or "/sessions/{session_id}" not in paths:
            continue
        for route in list(router.routes):
            if not (
                isinstance(route, APIRoute)
                and route.path == target_path
                and "POST" in route.methods
            ):
                continue
            original = route.endpoint

            def _make_patched(_original):
                """闭包持有原 endpoint。

                原 endpoint 不能作为带默认值的参数出现在 ``patched`` 签名里，
                否则 FastAPI 会把它当成普通请求参数，并尝试把函数对象序列化
                进 JSON Schema（产生 PydanticJsonSchemaWarning）。
                """
                async def patched(
                    body: CreateSessionRequest,
                    user_id: str = Depends(get_current_user_id),
                    workspace_manager=Depends(get_workspace_manager),
                    access=Depends(get_resource_access_service),
                ):
                    resp = await _original(
                        body,
                        user_id=user_id,
                        storage=storage,
                        workspace_manager=workspace_manager,
                        access=access,
                    )
                    await _apply_default_mode(
                        storage, user_id, body.agent_id, resp.session_id,
                    )
                    return resp

                return patched

            patched = _make_patched(original)

            new_route = APIRoute(
                path=route.path,
                endpoint=patched,
                response_model=route.response_model,
                methods=route.methods,
                name=route.name,
                tags=route.tags,
                summary="Create or resume a session (default permission mode applied)",
            )
            router.routes.remove(route)
            router.routes.append(new_route)
            replaced = True
            break
        if replaced:
            break
    if replaced:
        logger.info(
            "已包装 POST /sessions/：新会话默认权限模式 %s", DEFAULT_PERMISSION_MODE,
        )
    else:
        logger.warning("未找到 POST /sessions/ 路由，跳过权限模式 patch")
