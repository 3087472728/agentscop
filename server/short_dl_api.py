# -*- coding: utf-8 -*-
# ============================================================
# 模块名: short_dl_api.py
# 用途:   工作区文件下载短链——解决聊天链接被截断/令牌过期问题。
#
#         背景: 下载链接把签发令牌与完整 path（百分号编码的中文
#         长串）拼在 URL 里，经聊天消息展示/复制后屡次发生 URL
#         中段丢失，签名校验失败报 "Invalid download token"。
#
#         方案: 聊天里只放短链 /dl/<8位码>。点击时服务端根据
#         短码实时签发新下载令牌（TTL 同框架默认，补丁后 3600s），
#         302 跳转到 /workspace/files 完成下载。URL 短、无令牌、
#         永不过期（短码自身有效期默认 30 天，可配）。
#
#         迁移自服务器 /home/agentrunner/agentscop 未提交修改
#         （src/agentscop/short_dl_api.py，2026-11-12），适配本地
#         布局：路径取 server/paths.py，对外基地址默认本地直连。
#
# 接口:   POST /workspace/files/short-link   签发短链（供脚本调用，
#                                            参数同 download-token）
#         GET  /dl/{code}                    短链跳转（浏览器点击）
# 注册:   server/app.py 中 setup_short_dl(app, storage)
# ============================================================
import logging
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from agentscope.app._service._workspace import WorkspaceService
from agentscope.app.deps import get_current_user_id, get_workspace_service

from . import paths

logger = logging.getLogger("uvicorn.error")

# 短链库与角色表同目录（config/，随仓库备份策略）
DB_PATH = os.path.join(paths.CONFIG_DIR, "short_links.db")

# 短链对外基地址：
# - 本地开发（默认）：后端直连，无 /api 前缀
# - 容器/网关部署：设 AGENTSCOPE_PUBLIC_API_BASE 指向 gateway
#   （如 http://<host>:8090/api，网关剥掉 /api 前缀后转发）
PUBLIC_API_BASE = os.environ.get(
    "AGENTSCOPE_PUBLIC_API_BASE", "http://127.0.0.1:8020"
).rstrip("/")

# 短码有效期（秒），默认 30 天
SHORT_LINK_TTL = int(os.environ.get("AGENTSCOPE_SHORT_LINK_TTL", str(30 * 86400)))

router = APIRouter()
_db_lock = threading.Lock()

# 后端 storage 单例，setup() 时由 app.py 注入（属主回退查询用）
app_storage = None


def _init_db() -> None:
    """确保短链库目录与表结构就绪。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _db_lock, _con() as con:
        con.execute("SELECT 1 FROM short_links LIMIT 1")


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS short_links (
               code       TEXT PRIMARY KEY,
               agent_id   TEXT NOT NULL,
               session_id TEXT NOT NULL,
               path       TEXT NOT NULL,
               user_id    TEXT NOT NULL,
               created_at REAL NOT NULL)"""
    )
    return con


async def _find_session_owner(agent_id: str, session_id: str) -> str | None:
    """按 (agent_id, session_id) 跨属主查会话真实属主。

    本地迁移断层：从服务器迁移的库中会话属主是登录名
    （如 "agentrunner"），而本地 file 模式身份是派生 id
    （如 "u_UiVSPH6ihM"），get_session 按属主匹配必然落空。
    服务器 Redis 环境（身份=登录名=属主）不会走到这里。
    """
    from sqlalchemy import select

    from agentscope.app.storage._sql._tables import SessionRow

    assert app_storage is not None, "setup() 尚未注入 storage 单例"

    async with app_storage._session() as sess:
        row = (
            await sess.execute(
                select(SessionRow).where(
                    SessionRow.agent_id == agent_id,
                    SessionRow.id == session_id,
                )
            )
        ).scalar_one_or_none()
    return row.user_id if row else None


async def _resolve_owner(
    user_id: str,
    agent_id: str,
    session_id: str,
    workspace_service: WorkspaceService,
) -> str:
    """确定短链/令牌应归属的用户（必须是会话属主）。

    优先按当前身份解析（属主本人或服务器语义下的代理）；
    身份与属主因本地迁移断层不一致时，回退取真实属主。
    """
    try:
        await workspace_service.resolve(user_id, agent_id, session_id)
        return user_id
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    owner = await _find_session_owner(agent_id, session_id)
    if owner is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id!r} not found.",
        )
    await workspace_service.resolve(owner, agent_id, session_id)
    return owner


@router.post("/workspace/files/short-link")
async def create_short_link(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(..., description="会话工作区相对路径（与 download-token 一致）"),
    user_id: str = Depends(get_current_user_id),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> dict:
    """签发下载短链。与 download-token 同参同鉴权，供 make_dl_link.py 调用。

    仅校验会话可解析（与框架 download-token 一致的早期失败语义）；
    文件是否存在由点击时的真实下载验证。
    """
    owner = await _resolve_owner(
        user_id, agent_id, session_id, workspace_service
    )

    with _db_lock, _con() as con:
        # 撞码重试（8 字符 urlsafe，空间足够大，实际不会撞）
        for _ in range(5):
            code = secrets.token_urlsafe(6)
            try:
                con.execute(
                    "INSERT INTO short_links VALUES (?,?,?,?,?,?)",
                    (code, agent_id, session_id, path, owner, time.time()),
                )
                break
            except sqlite3.IntegrityError:
                continue
        else:
            raise HTTPException(status_code=500, detail="短码生成失败，请重试")

    logger.info(
        "短链已签发: /dl/%s -> %s (session=%s, owner=%s)",
        code, path, session_id, owner,
    )
    return {"code": code, "url": f"{PUBLIC_API_BASE}/dl/{code}", "ttl": SHORT_LINK_TTL}


@router.get("/dl/{code}")
async def short_link_download(
    code: str,
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> RedirectResponse:
    """短链跳转：按短码实时签发下载令牌，302 到真实下载地址。

    无需请求头鉴权——短码本身即能力凭证（与 URL 携带令牌同模型，
    8 字符 urlsafe 随机码，不可枚举）。
    """
    with _db_lock, _con() as con:
        row = con.execute(
            "SELECT agent_id, session_id, path, user_id, created_at "
            "FROM short_links WHERE code=?",
            (code,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="下载短链不存在（可能已被重新签发）")
    agent_id, session_id, path, user_id, created_at = row
    if time.time() - created_at > SHORT_LINK_TTL:
        raise HTTPException(status_code=410, detail="下载短链已过期，请重新生成")

    # 实时签发短时令牌（TTL 同框架默认，补丁后 3600s），随跳转 URL 携带
    token, _ = workspace_service.sign_download_token(user_id, path)
    target = (
        f"{PUBLIC_API_BASE}/workspace/files"
        f"?agent_id={urllib.parse.quote(agent_id)}"
        f"&session_id={urllib.parse.quote(session_id)}"
        f"&path={urllib.parse.quote(path, safe='')}"
        f"&download=true&token={token}"
    )
    return RedirectResponse(target, status_code=302)


def setup(app, storage) -> None:
    """注册短链路由（幂等：重复调用会重复注册，仅限启动期调用一次）。"""
    global app_storage
    app_storage = storage
    _init_db()
    app.include_router(router)
    logger.info(
        "下载短链服务已启用: POST /workspace/files/short-link, GET /dl/{code}"
    )

