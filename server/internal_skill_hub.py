# -*- coding: utf-8 -*-
# ============================================================
# 模块名: internal_skill_hub.py
# 用途:   公司内部技能来源（Hub），实现 AgentScope SkillHubBase 接口。
#         当前从服务器本地目录 server/internal_skills/ 读取技能
#         （每个子目录一个技能，必须含 SKILL.md）；将来云效自建
#         skills 库上线后，沿用同一 hub_id 只替换数据源实现即可，
#         前端与已安装技能零改动。
# 注册:   app_factory.py 的 skill_hubs=[...] 中加入 InternalSkillHub()
# ============================================================
"""公司内部技能库（Hub）。

数据源（当前阶段）：本地目录 ``<项目>/server/internal_skills/``，结构::

    internal_skills/
      <skill_id>/            # 子目录名即技能 ID（card_id）
        SKILL.md             # 必需，frontmatter 提供 name/description 等
        ...                  # 技能附带脚本/资料，随 zip 一起安装

SKILL.md frontmatter 支持字段：``name``（展示名）、``description``、
``version``、``license``、``tags``（列表）、``author``。
管理员把技能目录放进 internal_skills/ 即完成上架，删除即下架，
无需重启服务（元数据按文件 mtime 自动刷新）。

权限：SkillHubBase 的三个接口都携带 ``user_id``。默认所有登录用户
可见可装（框架路由已做登录认证）；传 ``allowed_users`` 可限制为
指定用户集合。将来对接角色系统时，仅需在 ``_check_user`` 中读取
角色配置。

将来接入云效 skills 库：框架按 ``(hub_id, card_id)`` 全局定位一张
技能卡，已安装记录（SkillRecord）也持久化了 hub_id——因此本 hub
的 ``hub_id="internal-skills"`` 必须长期稳定。云效库就绪后二选一：

1. 在本类中把 ``_scan / _read_meta / _pack`` 三个数据访问方法改为
   调云效 API（列目录 / 拉元数据 / 下载归档），hub_id 与前端不动；
2. 或新写 ``YunxiaoSkillHub(SkillHubBase)`` 并在 app_factory 替换
   注册项，hub_id 仍保持 ``internal-skills``，已安装技能无感迁移。
"""
from __future__ import annotations

import asyncio
import io
import os
import zipfile
from pathlib import Path
from typing import AsyncIterator

from agentscope.app.hub._error import HubError
from agentscope.app.hub._skill._base import SkillArchive, SkillHubBase
from agentscope.app.hub._skill._card import SkillCard, SkillHubPage

_CHUNK = 64 * 1024
# 打包时跳过的杂项文件
_IGNORED = {".DS_Store", "Thumbs.db", "desktop.ini"}
_IGNORED_DIRS = {"__pycache__", ".git", ".svn"}


class InternalSkillHub(SkillHubBase):
    """公司内部技能库：本地目录驱动，预留云效数据源替换。"""

    def __init__(
        self,
        hub_id: str = "internal-skills",
        display_name: str = "公司内部技能库",
        description: str = (
            "公司自建技能（当前由服务器 internal_skills/ 目录提供，"
            "将来由云效 skills 库提供）。"
        ),
        icon_url: str | None = None,
        *,
        root_dir: str | None = None,
        allowed_users: set[str] | None = None,
    ) -> None:
        """初始化。

        Args:
            hub_id (`str`, defaults to ``"internal-skills"``):
                稳定标识（路由段 + 已安装记录的外键），**不要改动**。
            display_name (`str`):
                前端技能市场里展示的来源名称。
            description (`str`):
                来源描述。
            icon_url (`str | None`):
                来源图标，None 时 UI 自动回退。
            root_dir (`str | None`):
                技能目录根路径，默认 ``<server>/internal_skills/``。
            allowed_users (`set[str] | None`):
                可浏览/安装的用户白名单；None 表示全部登录用户。
        """
        super().__init__(hub_id, display_name, description, icon_url)
        self.root_dir = Path(
            root_dir
            or os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "internal_skills",
            ),
        )
        self.allowed_users = allowed_users
        # skill_id -> (mtime, SkillCard 字段 dict)；mtime 变化自动重读
        self._meta_cache: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------------
    # 权限
    # ------------------------------------------------------------------
    def _check_user(self, user_id: str) -> None:
        """白名单为空即全部放行；否则仅白名单用户可见。

        将来对接角色系统时改这里（如查 role_access_policy）。
        抛 HubError(403)，由框架全局异常处理器转为 HTTP 403。
        """
        if self.allowed_users is not None and user_id not in self.allowed_users:
            raise HubError(
                self.hub_id,
                403,
                f"user {user_id!r} has no access to the internal skill hub",
            )

    # ------------------------------------------------------------------
    # 数据源（将来接云效时替换这三个方法的实现）
    # ------------------------------------------------------------------
    def _scan(self) -> list[str]:
        """列出全部技能 ID（含 SKILL.md 的子目录名，有序）。"""
        if not self.root_dir.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self.root_dir.iterdir()
            if entry.is_dir()
            and not entry.name.startswith(".")
            and (entry / "SKILL.md").is_file()
        )

    async def _meta(self, skill_id: str) -> dict:
        """读取单个技能的 SKILL.md frontmatter 元数据（mtime 缓存）。"""
        path = self.root_dir / skill_id / "SKILL.md"
        mtime = path.stat().st_mtime
        cached = self._meta_cache.get(skill_id)
        if cached and cached[0] == mtime:
            return cached[1]

        def _parse() -> dict:
            import frontmatter

            parsed = frontmatter.loads(path.read_text(encoding="utf-8"))
            tags = parsed.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            return {
                "display_name": parsed.get("name") or skill_id,
                "description": str(parsed.get("description") or ""),
                "version": parsed.get("version"),
                "license": parsed.get("license"),
                "tags": [str(t) for t in tags],
                "author": parsed.get("author"),
                "markdown": parsed.content,
            }

        meta = await asyncio.to_thread(_parse)
        self._meta_cache[skill_id] = (mtime, meta)
        return meta

    def _pack(self, skill_id: str) -> io.BytesIO:
        """把技能目录打包为 zip（顶层 ``<skill_id>/...``）。

        顶层结构与 GitHubSkillHub 的归一化一致：工作区安装器的
        ``_find_skill_root`` 依赖"单一顶层技能文件夹"识别技能根。
        """
        skill_dir = self.root_dir / skill_id
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
            for dirpath, dirnames, filenames in os.walk(skill_dir):
                dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
                for filename in filenames:
                    if filename in _IGNORED:
                        continue
                    full = Path(dirpath) / filename
                    arcname = f"{skill_id}/{full.relative_to(skill_dir)}"
                    zout.writestr(str(arcname), full.read_bytes())
        output.seek(0)
        return output

    # ------------------------------------------------------------------
    # SkillHubBase 接口
    # ------------------------------------------------------------------
    async def list_skills(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> SkillHubPage:
        """浏览/搜索内部技能目录。cursor 为 offset 字符串。"""
        self._check_user(user_id)
        skill_ids = self._scan()
        if q:
            # 按 ID、展示名、描述三重匹配
            lowered = q.lower()
            matched: list[str] = []
            for skill_id in skill_ids:
                meta = await self._meta(skill_id)
                if (
                    lowered in skill_id.lower()
                    or lowered in str(meta["display_name"]).lower()
                    or lowered in str(meta["description"]).lower()
                ):
                    matched.append(skill_id)
            skill_ids = matched

        offset = int(cursor) if cursor else 0
        page = skill_ids[offset : offset + limit]
        cards: list[SkillCard] = []
        for skill_id in page:
            meta = await self._meta(skill_id)
            cards.append(self._card(skill_id, meta))
        next_cursor = (
            str(offset + limit) if offset + limit < len(skill_ids) else None
        )
        return SkillHubPage(cards=cards, next_cursor=next_cursor)

    async def get_skill(self, user_id: str, card_id: str) -> SkillCard:
        """读取单个技能详情（含 SKILL.md 正文）。"""
        self._check_user(user_id)
        if card_id not in self._scan():
            raise KeyError(card_id)
        return self._card(card_id, await self._meta(card_id))

    async def download(
        self,
        user_id: str,
        card_id: str,
        version: str | None = None,
    ) -> SkillArchive:
        """打包技能目录为 zip 流供工作区安装器解压。"""
        self._check_user(user_id)
        if card_id not in self._scan():
            raise KeyError(card_id)
        buffer = await asyncio.to_thread(self._pack, card_id)

        async def _stream(buf: io.BytesIO) -> AsyncIterator[bytes]:
            buf.seek(0)
            while chunk := buf.read(_CHUNK):
                yield chunk

        return SkillArchive("zip", _stream(buffer))

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _card(self, skill_id: str, meta: dict) -> SkillCard:
        """组装 SkillCard。"""
        return SkillCard(
            hub_id=self.hub_id,
            id=skill_id,
            name=skill_id,
            display_name=meta.get("display_name"),
            description=meta.get("description", ""),
            tags=meta.get("tags") or [],
            version=meta.get("version"),
            author=meta.get("author") or "internal",
            markdown=meta.get("markdown"),
        )

    def __repr__(self) -> str:  # 便于日志排查
        return (
            f"<InternalSkillHub hub_id={self.hub_id!r} "
            f"root={self.root_dir} skills={len(self._scan())}>"
        )
