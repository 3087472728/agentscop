# -*- coding: utf-8 -*-
"""按用户隔离的工作区管理器：每个 (agent, user) 组合一个工作目录。

原版 ``LocalWorkspaceManager`` 把所有工作区映射到
``<basedir>/<agent_id>`` —— 与同一智能体对话的所有用户共享同一个
目录（记忆、卸载的产物、生成的文件）。本子类把边界收窄到用户：

    <basedir>/<agent_id>/users/<safe_user_id>/

长期记忆（``AgenticMemoryMiddleware`` 绑定 ``workspace.workdir``）、
会话产物（``sessions/<id>/``）以及智能体写入的所有文件因此自动
变为用户私有。技能分区同样继承这一隔离：每个用户的工作区装配
属于自己的技能副本。

工作区身份/缓存语义原样继承 —— 在 ``IsolationPolicy.PER_AGENT``
之下 id 本来就按 ``(user, agent)`` 组合派生，不同用户从不共享
缓存的工作区；本类只改变磁盘上的目录布局。
"""
import asyncio
import os
import re
import time

from agentscope.workspace import LocalWorkspace

from agentscope.app.workspace_manager import LocalWorkspaceManager

#: 无用户 id 请求的净化兜底目录（按理不应发生 —— 应用要求非空
#: X-User-ID —— 但缺失的身份绝不能映射到工作区根目录本身）。
ANONYMOUS_USER_DIR = "_anonymous"

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_\-]")
_MAX_COMPONENT_LEN = 64


def safe_user_component(user_id: str) -> str:
    """把用户 id 映射为单个安全的路径组件。

    用户 id 来自外部请求头，属于路径构造中的不可信输入：保留
    文件名友好字符，其余全部替换为 ``_``，并限制长度。
    """
    sanitized = _SAFE_COMPONENT.sub("_", str(user_id))[:_MAX_COMPONENT_LEN]
    return sanitized or ANONYMOUS_USER_DIR


class PerUserLocalWorkspaceManager(LocalWorkspaceManager):
    """按 (agent, user) 组合划分工作目录的本地工作区管理器。"""

    def workdir_for(self, agent_id: str, user_id: str) -> str:
        """为一个 (agent, user) 组合生成确定性的磁盘工作目录。"""
        return os.path.normpath(
            os.path.join(
                self._basedir,
                agent_id,
                "users",
                safe_user_component(user_id),
            )
        )

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> LocalWorkspace:
        """返回该用户的工作区，沿用父类的 TTL 缓存/二次检查
        模式，仅工作目录换成按用户划分的路径。"""
        if not workspace_id:
            workspace_id = await self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id="",
            )

        user_workdir = self.workdir_for(agent_id, user_id)

        # 阶段一：命中缓存 + 收集过期条目。
        async with self._lock:
            now = time.monotonic()
            expired = self._pop_expired(now)
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, now)
                hit: LocalWorkspace | None = ws
            else:
                hit = None

        # 阶段二：在锁外并行关闭过期条目。
        if expired:
            await asyncio.gather(
                *(self._safe_close(ws) for ws in expired),
                return_exceptions=True,
            )

        if hit is not None:
            return hit

        # 阶段三：持锁构建。
        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

            ws = LocalWorkspace(
                workspace_id=workspace_id,
                workdir=user_workdir,
                default_mcps=self._default_mcps,
                skill_paths=self._skill_paths,
            )
            await ws.initialize()
            self._cache[workspace_id] = (ws, time.monotonic())
            return ws
