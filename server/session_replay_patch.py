#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
File:    server/session_replay_patch.py
Purpose: 调大会话事件重放日志上限（SESSION_REPLAY_MAX_LEN）。
Note:    SSE 端点（GET /sessions/{sid}/stream）在客户端（重）连时会重放
         当前运行的"事件日志"，前端靠它恢复进行中的回复（切换页面后
         返回、掉线重连等场景）。但默认上限 1000 条按"保留最后 N 条"
         裁剪——思考模型的 delta 流可达每秒 ~150 条，长回复运行十几秒
         后 REPLY_START / *_BLOCK_START 就被挤出日志，重连后只剩裸
         delta，前端无法重建回复，表现为"返回会话看不到正在对话的部分"。
         本补丁把上限调到 100000（约 11 分钟连续思考流），可用环境变量
         AGENTSCOPE_REPLAY_MAX_LEN 覆盖。内存开销：仅运行中会话持有
         日志，运行结束 log_trim 清空；每条 delta 约数百字节，峰值
         单会话 ~40MB，本服务为单进程部署，可接受。
         兜底：即便仍超限被裁剪，前端 useMessages 也会按事件类型合成
         缺失的起始事件（重放自救），并在回复落库后重取完整消息。
"""

from __future__ import annotations

import logging
import os

from agentscope.app.message_bus._base import MessageBus
from agentscope.app.message_bus._keys import MessageBusKeys

logger = logging.getLogger("main.session_replay_patch")

_DEFAULT_MAX_LEN = 100_000


def setup() -> None:
    """替换会话事件重放日志上限（写入与重放读取共用同一常量）。"""
    try:
        max_len = int(os.environ.get("AGENTSCOPE_REPLAY_MAX_LEN", _DEFAULT_MAX_LEN))
    except ValueError:
        max_len = _DEFAULT_MAX_LEN
    max_len = max(1000, max_len)

    MessageBusKeys.SESSION_REPLAY_MAX_LEN = max_len
    # 兼容已废弃的 MessageBus.session_publish_event 路径（内部用同义的
    # 实例属性），一并调齐，避免两条路径行为不一致。
    MessageBus._SESSION_REPLAY_MAX_LEN = max_len  # noqa: SLF001

    logger.info("session replay log max_len -> %d", max_len)
