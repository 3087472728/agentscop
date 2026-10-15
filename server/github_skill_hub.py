# -*- coding: utf-8 -*-
# ============================================================
# 脚本名: github_skill_hub.py
# 用途:   GitHub 技能来源（Hub），从 skills.sh 相关开源仓库
#         （vercel-labs/agent-skills 与 vercel-labs/skills）提供
#         技能浏览/下载/安装能力（实现 AgentScope SkillHubBase 接口）
# 运行:   作为模块被 main.py 注入 agentscope-api 服务，无需单独启动
# 说明:   skills.sh 官方 API 需 Vercel OIDC token（本地不可用），
#         故改用 GitHub 公开 API + jsdelivr CDN + codeload 下载
# 修复:   多仓库解析。find-skills 位于 vercel-labs/skills（CLI 源码
#         仓库的 skills/ 子目录），不在 agent-skills 仓库中；旧版
#         单仓库硬编码导致库内导入报 "xxx: 'xxx'"（KeyError 透传）
# ============================================================
"""GitHub 技能来源（Hub）：从 skills.sh 的开源 GitHub 仓库提供技能。

skills.sh 的官方 API 需要 Vercel OIDC token（只能在 Vercel 部署环境内
签发），本地无法直接接入。但其技能全部开源在 GitHub——本 Hub 通过
GitHub 公开 API 实现 AgentScope 的 SkillHubBase 接口，支持多个仓库：

- vercel-labs/agent-skills：skills.sh 主技能仓库
- vercel-labs/skills：skills CLI 源码仓库，其 skills/ 子目录包含
  find-skills 等官方技能（作为补充来源）

接口实现：

- list_skills：contents API 列出各仓库 skills/ 目录（轻量、一次返回
  全部技能名），本地过滤/分页（cursor 为 offset 字符串），TTL 缓存
  节省配额。
- get_skill：jsdelivr CDN 读取 SKILL.md（raw.githubusercontent.com 在
  部分网络不可达），失败回退 GitHub contents API。
- download：codeload.github.com 下载仓库 zip 提取目标技能目录；若该
  域名不可达，回退 contents API 逐个拉取技能目录内文件重新打包。
"""
import asyncio
import base64
import io
import time
import zipfile
from typing import Any, AsyncIterator

from agentscope.app.hub._error import HubError
from agentscope.app.hub._skill._base import SkillArchive, SkillHubBase
from agentscope.app.hub._skill._card import SkillCard, SkillHubPage

# 主技能仓库（skills.sh 的技能主体）
DEFAULT_REPO = "vercel-labs/agent-skills"
DEFAULT_BRANCH = "main"
# 多仓库清单：按顺序解析，先到先得。vercel-labs/skills 是 skills CLI 的
# 源码仓库，其 skills/ 子目录包含 find-skills 等官方技能；anthropics/skills
# 是 Anthropic 官方技能仓库（docx/pdf/pptx/xlsx 等）。
DEFAULT_REPOS: list[tuple[str, str]] = [
    ("vercel-labs/agent-skills", "main"),
    ("vercel-labs/skills", "main"),
    ("anthropics/skills", "main"),
]
# 仓库内技能统一放在 skills/ 子目录下，如 skills/find-skills/SKILL.md
SKILLS_DIR = "skills"
_CHUNK = 64 * 1024


class GitHubSkillHub(SkillHubBase):
    """A skill hub backed by the official skills.sh GitHub repositories.

    The registry (skills.sh) requires a Vercel OIDC token that is only
    mintable inside a Vercel deployment, so this hub talks to GitHub
    instead, where every listed skill is open source. Multiple repos
    are searched in order, so skills published outside the main
    registry repo (e.g. find-skills in vercel-labs/skills) resolve too.
    """

    def __init__(
        self,
        hub_id: str = "github-skills",
        display_name: str = "Skills.sh (GitHub)",
        description: str = (
            "skills.sh 开源技能合集（vercel-labs/agent-skills、"
            "vercel-labs/skills 与 anthropics/skills），含 "
            "web-design-guidelines、find-skills、docx/pdf/pptx/xlsx "
            "等官方技能。"
        ),
        icon_url: str | None = "https://github.com/vercel-labs.png?size=64",
        *,
        repo: str = DEFAULT_REPO,
        branch: str = DEFAULT_BRANCH,
        extra_repos: list[tuple[str, str]] | None = None,
        timeout: float = 30.0,
        cache_ttl: float = 600.0,
    ) -> None:
        super().__init__(hub_id, display_name, description, icon_url)
        # 解析顺序：主仓库在前，其余按 DEFAULT_REPOS / extra_repos 追加
        self.repos: list[tuple[str, str]] = [(repo, branch)]
        for entry in extra_repos if extra_repos is not None else DEFAULT_REPOS:
            if entry not in self.repos:
                self.repos.append(entry)
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._client: Any = None
        # repo -> (monotonic 时间戳, 技能名列表)
        self._names_cache: dict[str, tuple[float, list[str]]] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "GitHubSkillHub":
        import httpx

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=8.0),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> Any:
        """返回共享客户端，未进入 app 生命周期时按需打开。"""
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=8.0),
                follow_redirects=True,
            )
        return self._client

    # ------------------------------------------------------------------
    # 上游数据（全部走 GitHub contents API，轻量且不依赖大 JSON）
    # ------------------------------------------------------------------
    async def _names_for(self, repo: str, branch: str) -> list[str]:
        """单个仓库 skills/ 目录下所有技能目录名，TTL 缓存。"""
        cached = self._names_cache.get(repo)
        now = time.monotonic()
        if cached and now - cached[0] < self.cache_ttl:
            return cached[1]

        url = (
            f"https://api.github.com/repos/{repo}"
            f"/contents/{SKILLS_DIR}?ref={branch}"
        )
        response = await self._http().get(url)
        if response.status_code >= 400:
            raise HubError(
                self.hub_id,
                response.status_code,
                response.text[:300],
            )

        names = sorted(
            item["name"]
            for item in response.json()
            if item.get("type") == "dir"
        )
        self._names_cache[repo] = (now, names)
        return names

    async def _skill_map(self) -> dict[str, tuple[str, str]]:
        """所有仓库的技能名 -> (repo, branch) 映射，先到先得。

        单个仓库失败（限流/网络）时跳过，仅当全部仓库都失败才抛出
        首个异常，保证部分可用时目录仍能列出。
        """
        mapping: dict[str, tuple[str, str]] = {}
        first_error: HubError | None = None
        for repo, branch in self.repos:
            try:
                names = await self._names_for(repo, branch)
            except HubError as exc:
                if first_error is None:
                    first_error = exc
                continue
            for name in names:
                mapping.setdefault(name, (repo, branch))
        if not mapping and first_error is not None:
            raise first_error
        return mapping

    async def _locate(
        self, card_id: str
    ) -> tuple[str, str] | None:
        """定位技能所在仓库；目录不可用或未收录时返回 None。"""
        try:
            mapping = await self._skill_map()
        except HubError:
            return None
        return mapping.get(card_id)

    async def _skill_names(self) -> list[str]:
        """全部仓库技能名的并集（有序），供浏览/搜索使用。"""
        return sorted(await self._skill_map())

    async def _iter_files(
        self, repo: str, branch: str, path: str
    ) -> AsyncIterator[tuple[str, bytes]]:
        """递归列出 contents 路径下所有文件（相对仓库根），读入内存。

        注意：contents API 列目录时返回的条目只有元数据，没有文件内容，
        因此每个文件需单独再请求一次 `contents/<path>` 才能拿到内容。
        """
        url = (
            f"https://api.github.com/repos/{repo}"
            f"/contents/{path}?ref={branch}"
        )
        response = await self._http().get(url)
        if response.status_code >= 400:
            return
        data = response.json()
        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get("type") == "dir":
                async for child in self._iter_files(
                    repo, branch, item["path"]
                ):
                    yield child
            elif item.get("type") == "file":
                file_url = (
                    f"https://api.github.com/repos/{repo}"
                    f"/contents/{item['path']}?ref={branch}"
                )
                file_resp = await self._http().get(file_url)
                if file_resp.status_code >= 400:
                    continue
                file_data = file_resp.json()
                if file_data.get("encoding") == "base64":
                    yield (
                        file_data.get("path", item["path"]),
                        base64.b64decode(file_data["content"]),
                    )
                else:
                    yield (
                        file_data.get("path", item["path"]),
                        file_data.get("content", "").encode(),
                    )

    async def _fetch_text(self, url: str) -> str | None:
        """GET 文本内容，任何失败（超时/非 2xx）返回 None。

        注意：客户端需 follow_redirects=True——jsdelivr 对部分文件会
        返回 301 跳转 raw.githubusercontent.com，若不跟随，301（<400）
        的重定向提示文本会被误当作 SKILL.md 内容。
        """
        try:
            response = await self._http().get(url)
            if response.status_code < 300:
                return response.text
        except Exception:
            pass
        return None

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
        """浏览或搜索技能目录。cursor 为 offset 字符串。"""
        mapping = await self._skill_map()
        names = sorted(mapping)
        if q:
            needle = q.lower()
            names = [name for name in names if needle in name.lower()]

        offset = int(cursor) if cursor else 0
        page = names[offset: offset + limit]
        cards = []
        for name in page:
            repo, _branch = mapping[name]
            cards.append(
                SkillCard(
                    hub_id=self.hub_id,
                    id=name,
                    name=name,
                    description="",
                    author=repo.rsplit("/", 1)[0],
                    url=f"https://skills.sh/{repo}/{name}",
                )
            )
        next_cursor = (
            str(offset + limit) if offset + limit < len(names) else None
        )
        return SkillHubPage(cards=cards, next_cursor=next_cursor)

    async def get_skill(self, user_id: str, card_id: str) -> SkillCard:
        """读取单个技能的 SKILL.md，填充描述与正文。"""
        import frontmatter

        # 定位技能所在仓库；找不到时保留 KeyError 语义（上游按此判断）
        located = await self._locate(card_id)
        if located is None:
            raise KeyError(card_id)
        repo, branch = located

        # 首选 jsdelivr CDN：raw.githubusercontent.com 在部分网络不可达
        text = await self._fetch_text(
            f"https://cdn.jsdelivr.net/gh/{repo}@{branch}"
            f"/{SKILLS_DIR}/{card_id}/SKILL.md"
        )
        if text is None:
            # 回退 GitHub contents API（内容为 base64）
            url = (
                f"https://api.github.com/repos/{repo}"
                f"/contents/{SKILLS_DIR}/{card_id}/SKILL.md?ref={branch}"
            )
            response = await self._http().get(url)
            if response.status_code == 404:
                raise KeyError(card_id)
            if response.status_code >= 400:
                raise HubError(
                    self.hub_id,
                    response.status_code,
                    response.text[:300],
                )
            payload = response.json()
            if payload.get("encoding") == "base64":
                text = base64.b64decode(payload["content"]).decode(
                    "utf-8", "replace"
                )
            else:
                text = payload.get("content", "")

        parsed = await asyncio.to_thread(frontmatter.loads, text)
        meta = {
            key: parsed.get(key)
            for key in ("license", "version")
            if parsed.get(key)
        }
        return SkillCard(
            hub_id=self.hub_id,
            id=card_id,
            name=card_id,
            display_name=parsed.get("name") or card_id,
            description=str(parsed.get("description") or ""),
            markdown=parsed.content,
            author=repo.rsplit("/", 1)[0],
            url=f"https://skills.sh/{repo}/{card_id}",
            metadata=meta,
        )

    async def download(
        self,
        user_id: str,
        card_id: str,
        version: str | None = None,
    ) -> SkillArchive:
        """下载技能并打包为 zip 流。codeload 不可用时回退 contents API。

        优先使用目录定位到的仓库；目录不可用时按 self.repos 顺序逐个
        尝试，全部落空才抛 KeyError。
        """
        order: list[tuple[str, str]] = []
        located = await self._locate(card_id)
        if located is not None:
            order.append(located)
        for entry in self.repos:
            if entry not in order:
                order.append(entry)

        for repo, branch in order:
            archive = await self._download_codeload(repo, branch, card_id)
            if archive is None:
                archive = await self._download_contents(
                    repo, branch, card_id
                )
            if archive is not None:
                return archive
        raise KeyError(card_id)

    async def _download_codeload(
        self, repo: str, branch: str, card_id: str
    ) -> SkillArchive | None:
        """codeload 整仓 zip，提取目标技能目录。不可达/不含目标返回 None。"""
        url = f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}"
        try:
            response = await self._http().get(url)
            if response.status_code >= 400:
                return None
        except Exception:
            return None

        slug = self._repo_slug(repo)
        source = io.BytesIO(response.content)
        output = io.BytesIO()
        # 归一化为 "<card_id>/..."：剥掉 zip 顶层目录与 skills/ 两级前缀，
        # 使工作区安装器 _find_skill_root 能识别单一顶层技能文件夹
        prefix = f"{slug}-{branch}/{SKILLS_DIR}/{card_id}/"
        top_len = len(f"{slug}-{branch}/{SKILLS_DIR}") + 1
        wrote = False
        try:
            with zipfile.ZipFile(source) as zin, zipfile.ZipFile(
                output, "w", zipfile.ZIP_DEFLATED
            ) as zout:
                for info in zin.infolist():
                    name = info.filename
                    if name.startswith(prefix) and not name.endswith("/"):
                        zout.writestr(info.filename[top_len:], zin.read(name))
                        wrote = True
        except zipfile.BadZipFile:
            return None

        if not wrote:
            return None

        output.seek(0)
        return SkillArchive("zip", self._stream(output))

    async def _download_contents(
        self, repo: str, branch: str, card_id: str
    ) -> SkillArchive | None:
        """contents API 递归拉取技能目录文件，重新打包为 zip。"""
        prefix = f"{SKILLS_DIR}/{card_id}/"
        output = io.BytesIO()
        wrote = False
        try:
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
                async for path, content in self._iter_files(
                    repo, branch, prefix[:-1]
                ):
                    # 归一化为 "<card_id>/..."，保持技能文件相对结构
                    zout.writestr(
                        f"{card_id}/{path[len(prefix):]}", content
                    )
                    wrote = True
        except Exception:
            return None

        if not wrote:
            return None

        output.seek(0)
        return SkillArchive("zip", self._stream(output))

    @staticmethod
    def _repo_slug(repo: str) -> str:
        """仓库短名，用于构造 codeload zip 顶层目录名。"""
        return repo.rsplit("/", 1)[-1]

    async def _stream(self, buffer: io.BytesIO) -> AsyncIterator[bytes]:
        """把内存 zip 缓冲切块异步送出。"""
        buffer.seek(0)
        while True:
            chunk = buffer.read(_CHUNK)
            if not chunk:
                break
            yield chunk
