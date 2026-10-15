# -*- coding: utf-8 -*-
# ============================================================
# 模块名: stuck_run_guard.py
# 用途:   僵死会话自动处理（用户无感自愈）。
#
#         背景: 每个会话同一时刻只允许一个回复任务在跑（ChatRunRegistry
#         强制）。任务正常结束会自动注销，但任务若卡死（如模型调用
#         无响应），注册表条目和会话锁就一直占着——用户再发消息只会
#         收到 409 "already has an active chat run"，且用户无法自行
#         解决。本模块做两层自动处理：
#
#         核心策略(忙时排队，非补丁式急救): 框架自带 WakeupDispatcher
#           排队调度器(其文档原话: 所有 run 触发经此串行消费者，使
#           并发 spawn 竞争"结构性不可能")。kind="message" 的 trigger
#           在会话忙时自动退避重入队(永不丢弃)，锁一释放即 spawn 正常
#           处理——消息落库、推理、SSE 推送全走框架原生路径。因此:
#
#           方案一(触发时排队): 包装 POST /chat/。撞 409 且输入是单条
#             用户消息时，消息入 wakeup 队列排队，返回 200——前端无感，
#             旧 run 结束后自动处理。多条消息/异常时回退自愈。
#           方案一兜底(触发时自愈): 排队不适用时，判定旧任务是否"僵死"
#             （超过阈值没有任何新消息落库），是则自动走框架标准中断
#             路径(interrupt)清理，再重试一次 spawn。
#           方案二(后台看门狗): 每 60s 扫描所有持锁会话，僵死的自动
#             中断并留痕；覆盖"排队的消息在等一个永不结束的僵死 run"
#             的场景——看门狗中断后锁释放，dispatcher 自动接续排队消息。
#
#         判僵死依据: messages 表该会话 max(created_at)。agent 每步
#         推理/工具结果都会落库，长任务中持续更新；长时间无新消息
#         即僵死。不看总时长（正常长任务可达 10+ 分钟，避免误杀）。
#         中断走 ChatService.interrupt：跨进程广播 → CancelDispatcher
#         取消任务 → agent 的 CancelledError 清理（伪造工具结果、
#         落 fallback 消息、ReplyEndEvent(INTERRUPTED)），锁与注册表
#         条目随任务退出自动释放，会话里留下"已中断"痕迹可追溯。
#
# 接口:   setup(app, storage) —— main.py 启动时调用一次
# 注意:   库内 created_at 为 UTC（本机时钟即 UTC，已实测一致），
#         统一用 datetime.utcnow() 对比。
# ============================================================
import asyncio
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, HTTPException
from fastapi.routing import APIRoute, _IncludedRouter

from agentscope.app._bus_ops import enqueue_run_trigger
from agentscope.app._router._schema import ChatRequest, ChatTriggerResponse
from agentscope.app.deps import (
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_message_bus,
)
from agentscope.app.message_bus import MessageBusKeys
from agentscope.message import Msg

logger = logging.getLogger("main.stuck_run_guard")

# ------------------------------------------------------------------
# 可调参数
# ------------------------------------------------------------------
IDLE_STALE_SECONDS = 1800   # 空闲阈值：超过 30 分钟无新消息 → 判僵死。
#   2026-09-23 事故: 原值 600s 曾把"单次脚本执行 10m6s"的活跃会话误判
#   为僵死而自动中断（单次工具调用执行期间不落库，空闲计时持续增长，
#   646s 时被看门狗斩断，任务未完成）。真僵死（模型流挂起）30 分钟
#   也足以确认；正常单次长工具调用极少超 30 分钟。
RETRY_WAIT_SECONDS = 15     # 自愈时等旧任务退出的最长时间
WATCHDOG_INTERVAL = 60      # 看门狗巡检间隔（秒）
HARD_CAP_SECONDS = 3600     # 单次回复硬上限（有进展但超 1 小时也中断）

from . import paths

DB_PATH = paths.DB_PATH  # data/agentscope.db（AGENTS_DB_PATH 可覆盖）

# 看门狗用：首次见到某会话持锁的时间（单进程内存态，重启即清零）
_first_seen: dict[str, float] = {}


# ------------------------------------------------------------------
# 判僵死：该会话最后一条消息距今多久
# ------------------------------------------------------------------
def _query_last_idle_seconds(session_id: str) -> float | None:
    """查 messages 表最后活动时间，返回空闲秒数；查不到返回 None。"""
    try:
        con = sqlite3.connect(DB_PATH, timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.error("[stuck-guard] 打开数据库失败: %s", exc)
        return None
    try:
        row = con.execute(
            "select max(created_at) from messages where session_id=?",
            (session_id,),
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return None
    try:
        last_dt = datetime.fromisoformat(str(row[0]))
    except ValueError:
        return None
    return (datetime.utcnow() - last_dt).total_seconds()


async def is_run_stale(
    session_id: str,
    idle_threshold: float = IDLE_STALE_SECONDS,
) -> bool:
    """判定会话当前 run 是否僵死（空闲超过阈值）。

    查不到任何活动记录时返回 False——宁可不动作，也不误杀。
    """
    idle = await asyncio.to_thread(_query_last_idle_seconds, session_id)
    if idle is None:
        return False
    if idle > idle_threshold:
        logger.info(
            "[stuck-guard] 会话 %s 已空闲 %.0f 秒（阈值 %.0f）",
            session_id, idle, idle_threshold,
        )
        return True
    return False


def _get_session_owner(session_id: str) -> tuple[str, str] | None:
    """查会话属主 (user_id, agent_id)，interrupt 调用需要。"""
    con = sqlite3.connect(DB_PATH, timeout=30)
    try:
        row = con.execute(
            "select user_id, agent_id from sessions where id=?",
            (session_id,),
        ).fetchone()
    finally:
        con.close()
    return (row[0], row[1]) if row else None


# ------------------------------------------------------------------
# 统一清理：走框架标准中断路径，并等注册表条目真正释放
# ------------------------------------------------------------------
async def cancel_stale_run(
    chat_service,
    registry,
    user_id: str,
    session_id: str,
    agent_id: str,
) -> bool:
    """中断会话的僵死 run 并等它退出。返回是否成功腾出位置。"""
    await chat_service.interrupt(user_id, session_id, agent_id)
    deadline = time.monotonic() + RETRY_WAIT_SECONDS
    while time.monotonic() < deadline:
        task = registry.get(session_id)
        if task is None or task.done():
            return True
        await asyncio.sleep(0.2)
    return False


# ------------------------------------------------------------------
# 方案一：包装 POST /chat/ —— 409 时判定僵死 → 自动清理 → 重试一次
# ------------------------------------------------------------------
async def _trigger_self_heal(
    chat_service, registry, user_id, session_id, agent_id, original_exc,
):
    """自愈流程；失败时抛回原 409，绝不把问题放大。"""
    try:
        ok = await cancel_stale_run(
            chat_service, registry, user_id, session_id, agent_id,
        )
    except LookupError:
        # 会话已不存在（被并发删除），维持原 409
        raise original_exc from None
    if not ok:
        logger.error(
            "[stuck-guard] 会话 %s 中断后 %ds 内未释放，返回原 409",
            session_id, RETRY_WAIT_SECONDS,
        )
        raise original_exc from None


def _wrap_chat_endpoint(app) -> None:
    """找到 chat 路由（path=/chat/，POST），替换为带自愈的版本。"""
    target_path = "/chat/"
    routers = [
        w.original_router
        for w in app.router.routes
        if isinstance(w, _IncludedRouter)
    ]
    routers.append(app.router)  # 兜底：路由未包 _IncludedRouter 时直接找
    for router in routers:
        for route in list(getattr(router, "routes", [])):
            if not (
                isinstance(route, APIRoute)
                and route.path == target_path
                and "POST" in route.methods
            ):
                continue
            original = route.endpoint

            def _make_patched(_original):
                """闭包持有原 endpoint（不能放进签名，避免 FastAPI 误当请求参数）。"""

                async def patched(
                    request: ChatRequest,
                    user_id: str = Depends(get_current_user_id),
                    chat_service=Depends(get_chat_service),
                    chat_run_registry=Depends(get_chat_run_registry),
                    message_bus=Depends(get_message_bus),
                ):
                    try:
                        return await _original(
                            request,
                            user_id=user_id,
                            chat_service=chat_service,
                            chat_run_registry=chat_run_registry,
                            message_bus=message_bus,
                        )
                    except HTTPException as exc:
                        if not (
                            exc.status_code == 409
                            and "active chat run" in str(exc.detail or "")
                        ):
                            raise
                        sid = request.session_id

                        # ── 首选：忙时排队（框架原生 message trigger）──
                        # 单条用户消息入 wakeup 队列：会话忙时 dispatcher
                        # 自动退避重入队（永不丢弃），旧 run 结束后锁一
                        # 释放即 spawn 正常处理——消息落库、推理、SSE 推
                        # 送全走框架原生路径，前端只看到回复稍晚到达。
                        if isinstance(request.input, Msg):
                            try:
                                await enqueue_run_trigger(
                                    message_bus,
                                    user_id=user_id,
                                    session_id=sid,
                                    agent_id=request.agent_id,
                                    kind=MessageBusKeys.WAKEUP_KIND_MESSAGE,
                                    inputs=request.input,
                                )
                                logger.info(
                                    "[stuck-guard] 会话 %s 忙，消息已排队，"
                                    "旧 run 结束后自动处理",
                                    sid,
                                )
                                return ChatTriggerResponse(
                                    status="started", session_id=sid,
                                )
                            except Exception:  # noqa: BLE001
                                logger.exception(
                                    "[stuck-guard] 会话 %s 消息排队失败，"
                                    "回退僵死自愈流程",
                                    sid,
                                )

                        # ── 兜底：排队不适用（多条消息/入队失败）时的自愈 ──
                        if not await is_run_stale(sid):
                            raise  # 还在正常干活（真并发）→ 维持 409
                        logger.warning(
                            "[stuck-guard] 会话 %s 触发 409 且判定僵死，自动中断重试",
                            sid,
                        )
                        await _trigger_self_heal(
                            chat_service, chat_run_registry,
                            user_id, sid, request.agent_id, exc,
                        )
                        try:
                            chat_run_registry.spawn(
                                chat_service.run(
                                    user_id=user_id,
                                    session_id=sid,
                                    agent_id=request.agent_id,
                                    input_msg=request.input,
                                ),
                                session_id=sid,
                            )
                        except RuntimeError:
                            raise exc from None  # 极端竞态：维持原 409
                        logger.info(
                            "[stuck-guard] 会话 %s 已恢复，消息正常发出", sid,
                        )
                        return ChatTriggerResponse(status="started", session_id=sid)

                return patched

            new_route = APIRoute(
                path=route.path,
                endpoint=_make_patched(original),
                response_model=route.response_model,
                methods=route.methods,
                name=route.name,
                tags=route.tags,
                summary=route.summary,
                description=route.description,
            )
            router.routes.remove(route)
            router.routes.append(new_route)
            logger.info("已包装 POST %s：僵死会话触发时自动自愈", target_path)
            return
    logger.warning("未找到 POST %s 路由，自愈 patch 未生效", target_path)


# ------------------------------------------------------------------
# 方案二：后台看门狗 —— 周期扫描持锁会话，僵死的自动中断
# ------------------------------------------------------------------
async def _locked_session_ids(bus) -> list[str]:
    """枚举当前持有会话锁的 session id。"""
    holders = getattr(bus, "_lock_holders", None)
    if holders is not None:
        prefix = MessageBusKeys._SESSION_LOCK.split("{sid}")[0]
        return [k[len(prefix):] for k in list(holders) if k.startswith(prefix)]
    # 兜底：结构变化时退回公开 API 逐个询问（近 24h 活跃会话）
    con = sqlite3.connect(DB_PATH, timeout=30)
    try:
        rows = con.execute(
            "select distinct session_id from messages "
            "where created_at >= datetime('now', '-1 day')"
        ).fetchall()
    finally:
        con.close()
    return [
        r[0]
        for r in rows
        if await bus.is_locked(MessageBusKeys.session_lock(r[0]))
    ]


async def _scan_once(app) -> None:
    bus = getattr(app.state, "message_bus", None)
    chat_service = getattr(app.state, "chat_service", None)
    registry = getattr(app.state, "chat_run_registry", None)
    if not (bus and chat_service and registry):
        return  # lifespan 尚未就绪（watchdog 先于原 lifespan 启动）
    for sid in await _locked_session_ids(bus):
        _first_seen.setdefault(sid, time.monotonic())
        held_for = time.monotonic() - _first_seen[sid]
        if held_for < HARD_CAP_SECONDS and not await is_run_stale(sid):
            continue
        owner = await asyncio.to_thread(_get_session_owner, sid)
        if owner is None:
            continue
        user_id, agent_id = owner
        reason = (
            f"已持续 {held_for / 60:.0f} 分钟(硬上限)"
            if held_for >= HARD_CAP_SECONDS
            else "空闲超时"
        )
        logger.warning(
            "[stuck-guard] 看门狗：会话 %s %s，自动中断", sid, reason,
        )
        try:
            await chat_service.interrupt(user_id, sid, agent_id)
        except LookupError:
            pass  # 会话已不存在
        except Exception:  # noqa: BLE001
            logger.exception("[stuck-guard] 会话 %s 自动中断失败", sid)
    # 清理已结束会话的记录
    for sid in [s for s in _first_seen if registry.get(s) is None]:
        _first_seen.pop(sid, None)


async def _watchdog_loop(app) -> None:
    logger.info(
        "[stuck-guard] 看门狗启动：每 %ds 巡检，空闲阈值 %ds，硬上限 %ds",
        WATCHDOG_INTERVAL, IDLE_STALE_SECONDS, HARD_CAP_SECONDS,
    )
    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            await _scan_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[stuck-guard] 看门狗本轮巡检异常，跳过")


def _wrap_lifespan(app) -> None:
    """在原 lifespan 外包一层，起/停看门狗后台任务。

    注意：create_app 传了自定义 lifespan，Starlette 会因此跳过
    add_event_handler("startup", ...)，所以只能包装 lifespan_context。
    """
    original = app.router.lifespan_context

    @asynccontextmanager
    async def patched_lifespan(a):
        task = asyncio.create_task(_watchdog_loop(a))
        try:
            async with original(a):
                yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("[stuck-guard] 看门狗退出异常")

    app.router.lifespan_context = patched_lifespan


# ------------------------------------------------------------------
# 入口
# ------------------------------------------------------------------
def setup(app, storage) -> None:
    """main.py 启动时调用一次：挂载方案一（路由自愈）与方案二（看门狗）。"""
    # uvicorn 不改根 logger 级别（默认 WARNING），INFO 会被吞；
    # 自带 handler 保证补丁动作在服务日志里可见、可追溯。
    if not logger.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(
            logging.Formatter("[%(asctime)s stuck-guard] %(message)s", "%H:%M:%S")
        )
        logger.addHandler(_h)
        logger.setLevel(logging.INFO)
    _wrap_chat_endpoint(app)
    _wrap_lifespan(app)
