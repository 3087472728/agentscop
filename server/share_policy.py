# -*- coding: utf-8 -*-
"""跨用户智能体共享策略：单一属主 + 显式授权。

模型（在 agentscope_extra/.env 中配置）：

- ``AGENTS_OWNER=admin`` —— 所有智能体的唯一属主。智能体不再按
  用户逐一注册；用户共享属主的那份副本。
- ``AGENTS_DEFAULT_SHARE=个人助手`` —— 该智能体对所有用户可见，
  无需任何配置。
- ``AGENTS_AGENT_SHARE=周报助手:u2;数据质检员:u3`` —— 显式授权：
  ``智能体名:用户1,用户2`` 规则之间用 ``;`` 分隔。权限为 READ ——
  被授权者可以创建会话并对话，但不能编辑该智能体。

其余一切（凭证、知识库、未授权的智能体）维持框架默认的全部拒绝。
不设置 ``AGENTS_OWNER`` 则本策略整体失效（等价于 DenyAll）。

共享智能体的*输出*隔离依赖按用户划分的工作目录布局
（``server/per_user_workspace.py``）：
``workspaces/<agent_id>/users/<user>/`` —— 在共享同一 agent_id 的
情况下，这段路径是用户之间唯一的文件级边界。
"""
import os
from pathlib import Path
from typing import Optional

from agentscope.app.access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)

from .auth import load_env_file

_EXTRA_ROOT = Path(__file__).resolve().parent.parent


def parse_share_rules(raw: str) -> dict[str, list[str]]:
    """把 ``名称:用户1,用户2;名称2:用户3`` 解析成 {name: [users]}。"""
    rules: dict[str, list[str]] = {}
    for rule in raw.split(";"):
        rule = rule.strip()
        if not rule or ":" not in rule:
            continue
        name, _, users = rule.partition(":")
        name = name.strip()
        viewers = [u.strip() for u in users.split(",") if u.strip()]
        if name and viewers:
            rules.setdefault(name, []).extend(viewers)
    return rules


class OwnedAgentsSharePolicy(ResourceAccessPolicyBase):
    """按"默认共享 + 显式授权"规则呈现属主名下的智能体。"""

    def __init__(
        self,
        owner: str,
        default_share: str = "",
        agent_share: Optional[dict[str, list[str]]] = None,
    ) -> None:
        self._owner = owner
        self._default_share = default_share.strip()
        self._agent_share = agent_share or {}

    @classmethod
    def from_env(cls) -> Optional["OwnedAgentsSharePolicy"]:
        """从 .env 构建；未配置属主时返回 ``None``。"""
        load_env_file()
        owner = os.environ.get("AGENTS_OWNER", "").strip()
        if not owner:
            return None
        return cls(
            owner=owner,
            default_share=os.environ.get("AGENTS_DEFAULT_SHARE", ""),
            agent_share=parse_share_rules(
                os.environ.get("AGENTS_AGENT_SHARE", "")
            ),
        )

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage,
    ) -> list[ResourceRef]:
        if kind != ResourceKind.AGENT or not self._owner:
            return []
        if viewer_id == self._owner:
            # 属主通过框架自身的属主范围读取看到自己的资源；
            # 无需跨属主引用。
            return []

        refs: list[ResourceRef] = []
        for record in await storage.list_agents(self._owner):
            name = record.data.name
            granted = name == self._default_share or viewer_id in (
                self._agent_share.get(name, [])
            )
            if granted:
                refs.append(
                    ResourceRef(
                        kind=kind,
                        owner_id=self._owner,
                        resource_id=record.id,
                        permission=ResourcePermission.READ,
                    )
                )
        return refs
