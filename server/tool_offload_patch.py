#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
File:    server/tool_offload_patch.py
Purpose: 方案 B —— 保留框架「工具超时卸载到后台」机制（并行能力、不独占
         会话），同时修复其 UI 假完成问题：
           1. 把卸载阈值从默认 10s 提高到可配置值（默认 45s），让多数
              任务在当前回复内同步跑完，只有真正的长任务才转后台；
           2. 卸载发生时向会话 SSE 流发布 CustomEvent
              (name="background_task_started")，前端据此显示
              「后台任务运行中」指示条，回复结束也不再显示假完成；
           3. 后台任务完成/取消/失败时发布 CustomEvent
              (name="background_task_finished")，前端据此清除指示条，
              随后 wakeup 唤醒的新一轮回复会把真实结果送达；
           4. 改写合成占位 ToolResponse 的文本语义：明确告知 LLM
              「这是通知不是结果」，缓解 agentscope issue #1711 中
              agent 误判任务已完成而提前收尾的问题。
Note:    实现方式为整体接管 ToolOffloadMiddleware.on_acting：完整复刻
         原实现（agentscope 2.0.7）的 drain-queue / 超时 / watcher /
         inbox+wakeup 逻辑，并在标注【PATCH】的位置注入上述行为。
         自定义事件发布走官方通道
         StateChangeMiddleware._publish_state 同款
         publish_session_event（写入 replay log + 实时扇出），
         SSE /stream 端点像普通会话事件一样带出，前端重连后通过
         事件回放自动恢复指示条状态。
         升级提示：若 agentscope 升级改变了 on_acting 的内部协议
         （drain 哨兵、ToolResponse 终止约定、bus 事件发布签名），
         需同步比对本文件与新版 _tool_offload_middleware.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from copy import deepcopy
from typing import AsyncGenerator, Callable

from agentscope.agent import Agent
from agentscope.app._bus_ops import deliver_to_inbox, publish_session_event
from agentscope.app.middleware._tool_offload_middleware import (
    _QUEUE_SENTINEL,
    ToolOffloadMiddleware,
)
from agentscope.event import CustomEvent
from agentscope.message import DataBlock, HintBlock, TextBlock, ToolResultState
from agentscope.tool import ToolChunk, ToolResponse

logger = logging.getLogger("main.tool_offload_patch")

# 卸载阈值（秒）：工具执行超过该时长才转后台。
# 环境变量 TOOL_OFFLOAD_TIMEOUT_SECS 可覆盖；默认 45s。
_TIMEOUT_SECS: float = float(os.environ.get("TOOL_OFFLOAD_TIMEOUT_SECS", "45"))


async def _publish_bg_event(
    self: ToolOffloadMiddleware,
    session_id: str,
    name: str,
    value: dict,
) -> None:
    """向会话事件流发布后台任务 CustomEvent（官方 bus 通道）。"""
    try:
        event = CustomEvent(name=name, value=value)
        await publish_session_event(
            self._message_bus,
            session_id,
            event.model_dump(mode="json"),
        )
    except Exception:  # pylint: disable=broad-except
        # 事件通知失败不影响工具执行与结果投递主流程。
        logger.warning(
            "[tool-offload] 发布后台任务事件 %s 失败: session_id=%s",
            name,
            session_id,
            exc_info=True,
        )


async def _patched_acting(  # type:ignore[override]
    self,
    agent: Agent,
    input_kwargs: dict,
    next_handler: Callable[..., AsyncGenerator],
) -> AsyncGenerator[ToolChunk | ToolResponse, None]:
    """原 on_acting 逻辑 + 后台任务状态事件注入。"""
    timeout_secs = _TIMEOUT_SECS
    tool_call = input_kwargs["tool_call"]

    # ----------------------------------------------------------------
    # Guard：状态注入工具与外部工具永不卸载（与原实现一致，同步透传）。
    # ----------------------------------------------------------------
    tool = await agent.toolkit.get_tool(tool_call.name)
    if tool is not None and (
        tool.is_state_injected or tool.is_external_tool
    ):
        async for item in next_handler(**input_kwargs):
            yield item
        return

    # ----------------------------------------------------------------
    # 把 next_handler 包进 Task，输出经 Queue 转发（与原实现一致）。
    # ----------------------------------------------------------------
    queue: asyncio.Queue = asyncio.Queue()

    async def _drain_to_queue() -> None:
        try:
            async for item in next_handler(**input_kwargs):
                await queue.put(item)
        except Exception as exc:  # pylint: disable=broad-except
            await queue.put(exc)
        finally:
            await queue.put(_QUEUE_SENTINEL)

    drain_task: asyncio.Task = asyncio.create_task(_drain_to_queue())

    # ----------------------------------------------------------------
    # 在截止时间前消费队列；到点未完成则转后台（与原实现一致）。
    # ----------------------------------------------------------------
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_secs
    pre_collected: list = []
    completed = False

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            item = await asyncio.wait_for(
                queue.get(),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            break

        if item is _QUEUE_SENTINEL:
            completed = True
            break
        if isinstance(item, BaseException):
            drain_task.cancel()
            raise item

        pre_collected.append(item)
        if isinstance(item, ToolResponse):
            completed = True
            break

    if completed:
        for item in pre_collected:
            yield item
        drain_task.cancel()
        return

    # ----------------------------------------------------------------
    # 超时路径：注册后台任务 + spawn 投递 watcher + yield 合成占位。
    # ----------------------------------------------------------------
    session_id = agent.state.session_id
    tool_name = tool_call.name
    snapshot = list(pre_collected)

    logger.info(
        "[tool-offload] 工具 '%s' 超过 %.1fs，转入后台: session_id=%s, agent_id=%s",
        tool_name,
        timeout_secs,
        session_id,
        agent.name,
    )

    async def _deliver_when_done() -> None:
        """等待后台任务结束，把真实结果投递到会话收件箱 + 唤醒，
        并向前端发布 background_task_finished 事件。

        与原实现的差异：取消 / 失败路径也会发布 finished 事件
        （status 区分），确保前端指示条总能被清除。
        """
        status = "completed"
        try:
            await drain_task
        except asyncio.CancelledError:
            status = "cancelled"
            logger.info(
                "[tool-offload] 后台工具 '%s' 已取消，跳过投递: session_id=%s",
                tool_name,
                session_id,
            )
            await _publish_bg_event(
                self,
                session_id,
                "background_task_finished",
                {
                    "task_id": task_id,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call.id,
                    "session_id": session_id,
                    "status": status,
                },
            )
            return
        except Exception:  # pylint: disable=broad-except
            status = "failed"
            logger.warning(
                "[tool-offload] 后台工具 '%s' 失败，跳过投递: session_id=%s",
                tool_name,
                session_id,
                exc_info=True,
            )
            await _publish_bg_event(
                self,
                session_id,
                "background_task_finished",
                {
                    "task_id": task_id,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call.id,
                    "session_id": session_id,
                    "status": status,
                },
            )
            return

        remaining_items: list = []
        while not queue.empty():
            try:
                item = queue.get_nowait()
                if isinstance(item, ToolResponse):
                    remaining_items.append(item)
                    break
            except asyncio.QueueEmpty:
                break

        all_items = snapshot + remaining_items
        response: ToolResponse | None = next(
            (i for i in all_items if isinstance(i, ToolResponse)),
            None,
        )

        tool_call_id = tool_call.id

        hint_source = json.dumps(
            {
                "label": "tool_output",
                "sublabel": f"{tool_name} · {tool_call_id}",
            },
            ensure_ascii=False,
        )

        if response is None or len(response.content) == 0:
            hint = HintBlock(
                hint=(
                    f"<system-notification>"
                    f"Tool '{tool_name}' running in background "
                    f"(id={tool_call_id}) has completed with no output."
                    f"</system-notification>"
                ),
                source=hint_source,
            )
        else:
            content_blocks: list[TextBlock | DataBlock] = deepcopy(
                response.content,
            )
            prefix = (
                f"<system-notification>"
                f"Tool '{tool_name}' running in background "
                f"(id={tool_call_id}) has completed.\n\n"
                f"Result:\n\n"
            )
            if isinstance(content_blocks[0], TextBlock):
                content_blocks[0].text = prefix + content_blocks[0].text
            else:
                content_blocks.insert(0, TextBlock(text=prefix))

            suffix = "</system-notification>"
            if isinstance(content_blocks[-1], TextBlock):
                content_blocks[-1].text += suffix
            else:
                content_blocks.append(TextBlock(text=suffix))

            hint = HintBlock(
                hint=content_blocks,
                source=hint_source,
            )

        logger.info(
            "[tool-offload] 后台工具 '%s' 完成，投递结果并唤醒: session_id=%s",
            tool_name,
            session_id,
        )
        await deliver_to_inbox(
            self._message_bus,
            user_id=self._user_id,
            session_id=session_id,
            agent_id=self._agent_id,
            payload=hint.model_dump(mode="json"),
        )

        # 【PATCH】结果已投递，通知前端清除「后台运行中」指示。
        await _publish_bg_event(
            self,
            session_id,
            "background_task_finished",
            {
                "task_id": task_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call.id,
                "session_id": session_id,
                "status": status,
            },
        )

    # 先注册后台任务拿到 task_id（watcher 闭包里要引用），再 spawn watcher。
    task_id = await self._bg_manager.register_task(
        asyncio_task=drain_task,
        session_id=session_id,
        agent_id=self._agent_id,
        user_id=self._user_id,
        tool_name=tool_name,
    )
    asyncio.create_task(_deliver_when_done())

    # 【PATCH】卸载已发生，通知前端显示「后台任务运行中」。
    await _publish_bg_event(
        self,
        session_id,
        "background_task_started",
        {
            "task_id": task_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call.id,
            "session_id": session_id,
            "timeout_secs": timeout_secs,
        },
    )

    logger.info(
        "[tool-offload] 已为卸载工具 '%s' 产出合成占位响应: task_id=%s, session_id=%s",
        tool_name,
        task_id,
        session_id,
    )

    # 【PATCH】占位文本改写：明确「这是通知不是结果」，保留
    # <system-reminder> 包裹（LLM 可识别的框架提示语义）。
    placeholder_text = f"""<system-reminder>Tool '{tool_name}' \
(id={task_id}) has been running for over {timeout_secs}s and was moved \
to the background. This message is a NOTIFICATION, not the tool's result. \
The real result will arrive later as a new system notification message. \
Do NOT poll, query, or call any waiting tool such as `bash sleep` — you \
will be notified automatically when it finishes. You have exactly two \
valid options:
1. Continue with other independent tasks and ignore this tool for now; or
2. If there is nothing else to do, simply give a text reply without \
calling any tool, which ends the current reasoning loop.
</system-reminder>"""

    yield ToolChunk(
        content=[TextBlock(text=placeholder_text)],
        state=ToolResultState.SUCCESS,
    )

    yield ToolResponse(
        content=[TextBlock(text=placeholder_text)],
        state=ToolResultState.SUCCESS,
        id=tool_call.id,
    )


def setup() -> None:
    """接管工具卸载：保留后台机制 + 注入前端状态事件 + 调整阈值。"""
    ToolOffloadMiddleware.on_acting = _patched_acting  # type:ignore[assignment]
    logger.info(
        "[tool-offload] 方案 B 已启用：保留后台卸载（阈值 %.0fs，"
        "TOOL_OFFLOAD_TIMEOUT_SECS 可覆盖），卸载/完成时向 SSE 发布 "
        "background_task_started/finished 事件，占位文本已改写为"
        "「通知非结果」语义",
        _TIMEOUT_SECS,
    )
