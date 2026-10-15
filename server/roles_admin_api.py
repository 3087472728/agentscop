# -*- coding: utf-8 -*-
# ============================================================
# 模块名: roles_admin_api.py
# 用途:   团队角色管理 API（仅管理员）——数据事实源为 roles_store
#         （Redis roles:* 键族；AGENTS_ROLES_STORE=file 回退文件模式）：
#         GET  /me                  当前登录者身份与角色（任何用户可调）
#         GET  /roles               读取用户表（外部契约保持「登录名键」，
#                                   store 内部以不可变 id 为键，前端零改动）
#         POST /roles               CAS 整表保存（WATCH 版本号，并发冲突
#                                   返回 409 提示刷新）；含密码哈希化与
#                                   删除用户生命周期（409 资源决策流）
#         POST /roles/users/rename  用户改名——id 换键后仅改登录名，
#                                   资源 owner / 授权 / 磁盘目录零迁移
#         GET  /roles/export        导出同构 JSON 冷备（即回滚包）
#         GET  /roles/health        健康自检（连通性/AOF/冷备/补偿队列）
#         密码策略：GET 返回时 password 一律置空串（哈希不出网）；POST
#         保存时非 sha256$ 前缀视为明文自动哈希；已存在用户留空=保持原哈希
# 并发保护: 保存走 store CAS（WATCH roles:ver）；文件模式由 store 内部
#           文件锁互斥——本模块不再直接触碰 roles.json
# 接口:   setup(app, storage=None) —— 向应用注册路由，启动时调用一次；
#         storage 用于删除用户时的资源清点/转移/级联删除
# 依赖:   roles_store（唯一存储入口）、auth（角色/哈希）
# ============================================================
import re
import time
from pathlib import Path

from fastapi import Body, Depends, HTTPException

from agentscope.app.deps import get_current_user_id

from .auth import get_user_role, hash_password, is_hashed
from .roles_store import (
    ROLES_FILE,
    StoreConflict,
    StoreUnavailable,
    audit,
    get_store,
    new_user_id,
    to_name_keyed,
)

# ---------------------------------------------------------------------------
# 用户名白名单 —— 与 per_user_workspace.safe_user_component 的净化规则对齐：
# 入口即合法 ⇒ 净化为恒等变换 ⇒ 不可能出现两个用户名净化后碰撞同一目录。
# 磁盘隔离（工作区/记忆/技能）因此有了双层防御：入口白名单 + 净化兜底。
# 旧版共享/ACL 配置键（kb_shared_owners 等）已废弃：保存时重组用户表，
# 未知顶层键天然被丢弃，无需显式剔除。
# ---------------------------------------------------------------------------
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_username(name: object, field: str = "用户名") -> str:
    """校验用户名白名单，不合法抛 422 并点名。返回修剪后的合法名。"""
    name = (name or "").strip() if isinstance(name, str) else ""
    if not _USERNAME_RE.fullmatch(name):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{field}「{name}」不合法：仅允许字母、数字、下划线、横杠，"
                "长度 1-64（用于磁盘目录隔离，特殊符号会造成路径碰撞）"
            ),
        )
    return name


# ---------------------------------------------------------------------------
# 密码解析（保存路径专用；GET 出网脱敏由 to_name_keyed 完成）
# ---------------------------------------------------------------------------
def _resolve_password(name: str, info: dict, prev: dict | None) -> str:
    """就地规整 users 项的密码字段：

    · password 为 sha256$ 前缀 → 原样保留（前端回传兼容）
    · 其他非空 → 明文，哈希化
    · 空串/缺失 → 继承原哈希（留空即不改）；新用户无密码 → 422
      （避免造出无法登录的账号）
    """
    raw = info.get("password")
    if isinstance(raw, str) and raw:
        return raw if is_hashed(raw) else hash_password(raw)
    prev_pw = (prev or {}).get("password")
    if isinstance(prev_pw, str) and prev_pw:
        return prev_pw
    raise HTTPException(
        status_code=422,
        detail=f"用户 {name} 缺少密码（首次保存需设置初始密码，之后留空即不改）",
    )


# ---------------------------------------------------------------------------
# 资源清点 / 转移 / 级联删除（删除用户生命周期专用；
# 改名在 id 换键模型下 owner 不变，无需任何资源迁移）
# ---------------------------------------------------------------------------
#: 工作区根目录 —— 与 main.py 中 PerUserLocalWorkspaceManager 的 basedir 一致
WORKSPACES_DIR = Path(__file__).resolve().parent.parent / "workspaces"


def _workspace_dirs_of(user: str) -> list[str]:
    """列出该用户在磁盘上的全部工作目录（workspaces/<agent>/users/<user>/）。"""
    from .per_user_workspace import safe_user_component

    comp = safe_user_component(user)
    root = WORKSPACES_DIR
    if not root.is_dir():
        return []
    dirs: list[str] = []
    for agent_dir in sorted(root.iterdir()):
        user_dir = agent_dir / "users" / comp
        if user_dir.is_dir():
            dirs.append(str(user_dir))
    return dirs


def _resource_label(rec) -> str:
    """从资源记录里尽量取一个可读名称（仅用于 409 清单展示）。"""
    for attr in ("name",):
        val = getattr(rec, attr, None)
        if isinstance(val, str) and val:
            return val
    data = getattr(rec, "data", None)
    if isinstance(data, dict):
        val = data.get("name")
        if isinstance(val, str) and val:
            return val
    return str(getattr(rec, "id", "?"))


async def _inventory_user_resources(storage, user: str) -> dict[str, list[dict]]:
    """清点用户名下全部 Redis 资源（agent / 知识库 / 凭证）。"""
    if storage is None:
        return {}
    out: dict[str, list[dict]] = {}
    for kind, lister in (
        ("agents", "list_agents"),
        ("knowledge_bases", "list_knowledge_bases"),
        ("credentials", "list_credentials"),
    ):
        try:
            records = await getattr(storage, lister)(user)
        except Exception:
            records = []
        out[kind] = [
            {"id": getattr(r, "id", "?"), "name": _resource_label(r)}
            for r in records
        ]
    return out


def _inventory_is_empty(inv: dict[str, list[dict]]) -> bool:
    return not any(inv.values())


async def _transfer_user_resources_to_admin(storage, user: str) -> dict[str, int]:
    """把用户名下全部资源转移给 admin（幂等：已迁则跳过）。

    转移语义 = 「归属即规则」：agent 落入 admin 名下后按 user_agents
    白名单可见；KB/凭证变全员只读。原 owner 的会话/定时任务随
    delete_<kind> 级联清理（用户将不存在，其会话无人可用）。
    """
    if storage is None:
        return {}
    counts: dict[str, int] = {}

    for rec in await storage.list_credentials(user):
        rec.user_id = "admin"
        await storage.upsert_credential("admin", rec)
        await storage.delete_credential(user, rec.id)
        counts["credentials"] = counts.get("credentials", 0) + 1

    for rec in await storage.list_knowledge_bases(user):
        kb_id = rec.id
        # 文档记录随 KB 一起迁走，避免残留在旧 owner 名下变幻影
        try:
            docs = await storage.list_knowledge_documents(user, kb_id)
        except Exception:
            docs = []
        rec.user_id = "admin"
        await storage.upsert_knowledge_base("admin", rec)
        for doc in docs:
            doc.user_id = "admin"
            await storage.upsert_knowledge_document("admin", kb_id, doc)
            await storage.delete_knowledge_document(user, kb_id, doc.id)
        await storage.delete_knowledge_base(user, kb_id)
        counts["knowledge_bases"] = counts.get("knowledge_bases", 0) + 1

    # agent 最后迁：delete_agent 会级联删除旧 owner 名下的会话与定时任务
    for rec in await storage.list_agents(user):
        rec.user_id = "admin"
        await storage.upsert_agent("admin", rec)
        await storage.delete_agent(user, rec.id)
        counts["agents"] = counts.get("agents", 0) + 1

    return counts


async def _cascade_delete_user_resources(storage, user: str) -> dict[str, int]:
    """连带删除用户名下全部资源（不可逆，需前端二次确认后才调用）。"""
    if storage is None:
        return {}
    counts: dict[str, int] = {}
    for rec in await storage.list_credentials(user):
        await storage.delete_credential(user, rec.id)
        counts["credentials"] = counts.get("credentials", 0) + 1
    for rec in await storage.list_knowledge_bases(user):
        await storage.delete_knowledge_base(user, rec.id)
        counts["knowledge_bases"] = counts.get("knowledge_bases", 0) + 1
    for rec in await storage.list_agents(user):
        # delete_agent 级联删除该 agent 的会话、定时任务与团队引用
        await storage.delete_agent(user, rec.id)
        counts["agents"] = counts.get("agents", 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------
def setup(app, storage=None) -> None:
    """向 FastAPI 应用注册 /roles 管理路由。

    ``storage``：RedisStorage 实例（main.py 注入）。为 None 时跳过
    删除生命周期的资源清点/转移（仅影响删人决策流，不影响基础管理）。
    注入的 ``user_id`` 是不可变 id（auth.basic_auth_user 已完成
    登录名→id 翻译），对外契约仍以登录名呈现——前端零改动。
    """

    @app.get("/me")
    async def me(
        user_id: str = Depends(get_current_user_id),
    ) -> dict:
        """当前登录者身份与角色——前端 admin 判定的唯一数据源。"""
        data = await get_store().load_all()
        info = (data.get("users") or {}).get(user_id) or {}
        return {
            "user_id": user_id,
            "name": info.get("name") or user_id,
            "role": await get_user_role(user_id),
        }

    @app.get("/roles")
    async def get_roles(
        user_id: str = Depends(get_current_user_id),
    ) -> dict:
        await _require_admin(user_id)
        try:
            data = await get_store().load_all()
        except StoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return to_name_keyed(data, keep_password=False)

    @app.post("/roles")
    async def save_roles(
        payload: dict = Body(...),
        user_id: str = Depends(get_current_user_id),
    ) -> dict:
        """CAS 整表保存：读时记版本 → 组装 id 键新表 → WATCH 校验写回。

        外部契约仍为「登录名键」；组装时翻译为内部 id 键——存量用户
        继承 id（资源/授权/目录归属零迁移），新增用户现场派生随机 id，
        admin 字面量豁免。版本被抢先 → 409 请刷新重试。
        """
        await _require_admin(user_id)
        store = get_store()
        try:
            current = await store.load_all()
        except StoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        users_in = payload.get("users")
        if not isinstance(users_in, dict) or not users_in:
            raise HTTPException(status_code=422, detail="缺少 users 字段")

        cur_users = current.get("users") or {}
        by_name = {i.get("name") or uid: (uid, i) for uid, i in cur_users.items()}
        if "admin" not in users_in and "admin" in by_name:
            # 契约延续：超级管理员必须始终在册（原样保留 role/密码）
            users_in["admin"] = {"role": by_name["admin"][1].get("role", "admin")}

        # ── 组装新表（id 键）+ 密码规整 ─────────────────────────────
        next_users: dict[str, dict] = {}
        for name, info in users_in.items():
            if not isinstance(info, dict):
                continue
            prev_uid, prev = by_name.get(name, (None, None))
            if name == "admin":
                uid = "admin"
            elif prev_uid:
                uid = prev_uid
            else:
                # 用户名白名单只管新增：入口合法 ⇒ 磁盘净化恒等 ⇒ 无碰撞；
                # 存量键豁免（不阻塞历史数据管理）
                validate_username(name)
                uid = new_user_id()
            record: dict = {"id": uid, "name": name,
                            "role": info.get("role", "member")}
            if info.get("team"):
                record["team"] = info["team"]
            record["password"] = _resolve_password(name, info, prev)
            next_users[uid] = record
        if "admin" not in next_users:
            raise HTTPException(
                status_code=422, detail="users 必须包含超级管理员 admin")

        # ── 删除用户生命周期：diff 出被删用户，清点名下资源 ──────────
        removed = sorted(set(by_name) - set(users_in))
        resolution = payload.pop("resolution", None) or {}
        archived_dirs: list[str] = []
        transfers: dict[str, dict] = {}
        for name in removed:
            uid = by_name[name][0]
            if uid == "admin":
                continue
            dirs = _workspace_dirs_of(uid)
            action = resolution.get(name)
            if storage is not None:
                inv = await _inventory_user_resources(storage, uid)
                if _inventory_is_empty(inv):
                    action = action or "skip"
                elif action not in ("transfer", "delete"):
                    # 名下有资源且管理员未表态 → 409 决策流，
                    # 绝不静默保存（否则产生谁也看不见的幻影资源）
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": (
                                f"用户 {name} 名下仍有资源，请选择处置方式"
                                "（transfer=转移给 admin / delete=连带删除）"
                            ),
                            "resources": {name: inv},
                            "workspace_dirs": dirs,
                        },
                    )
            if action == "transfer":
                transfers[name] = await _transfer_user_resources_to_admin(
                    storage, uid
                )
            elif action == "delete":
                await _cascade_delete_user_resources(storage, uid)
            archived_dirs.extend(dirs)

        # ── user_agents：登录名键 → id 键（未知名静默丢弃） ──────────
        next_ua: dict[str, list] = {}
        for name, allow in (payload.get("user_agents") or {}).items():
            uid = "admin" if name == "admin" else by_name.get(name, (None,))[0]
            if uid:
                next_ua[uid] = allow

        # ── CAS 写回（版本被抢先 → 409；降级/AOF 未开 → 503） ────────
        next_data = {
            "admin_edit": bool(payload.get("admin_edit",
                                           current.get("admin_edit", True))),
            "users": next_users,
            "user_agents": next_ua,
        }
        try:
            await store.save_all(next_data, int(current.get("_ver", 0)),
                                 actor=user_id)
        except StoreConflict as exc:
            raise HTTPException(
                status_code=409,
                detail=f"用户表已被其他管理员修改，请刷新后重试（{exc}）",
            ) from exc
        except StoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return {
            "ok": True,
            "transferred": transfers,
            # 被删用户的磁盘工作目录清单（程序不自动删，提示管理员归档，
            # 可用 scripts/archive_user_workspace.py 重命名打标）
            "archived_dirs": archived_dirs,
        }

    @app.post("/roles/users/rename")
    async def rename_user(
        payload: dict = Body(...),
        user_id: str = Depends(get_current_user_id),
    ) -> dict:
        """用户改名：id 换键后仅改登录名（store Lua 原子）。

        id 不可变 ⇒ 资源 owner / user_agents / 会话归属 / 磁盘目录
        （目录以 id 净化名命名）全部零迁移；旧三步迁移退化为一次
        users.name 改写 + roles:name 索引维护 + INCR ver。
        """
        await _require_admin(user_id)
        store = get_store()
        old = validate_username(payload.get("from"), "原用户名")
        new = validate_username(payload.get("to"), "新用户名")
        if old == "admin" or new == "admin":
            raise HTTPException(
                status_code=422, detail="超级管理员账号 admin 不可参与改名")
        if old == new:
            raise HTTPException(status_code=422, detail="新用户名与原名相同")
        try:
            uid = await store.resolve(old)
            if not uid:
                raise HTTPException(status_code=404, detail=f"用户 {old} 不存在")
            current = await store.load_all()
            if any(i.get("name") == new for i in (current.get("users") or {}).values()):
                raise HTTPException(status_code=422, detail=f"用户 {new} 已存在")
            await store.rename_user(uid, new)
        except StoreConflict as exc:
            raise HTTPException(
                status_code=409, detail=f"并发冲突，请重试（{exc}）") from exc
        except StoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"用户 {old} 不存在") from exc
        return {"ok": True, "id": uid, "from": old, "to": new}

    @app.get("/roles/export")
    async def export_roles(
        user_id: str = Depends(get_current_user_id),
    ) -> dict:
        """导出用户表为旧 roles.json 同构 JSON（冷备即回滚包，9.9-2/10）。

        Redis 模式走 backup_now（登录名键 + 保留密码哈希，落盘
        config/role_backups/ 并轮转）；文件模式 roles.json 本身即备份。
        """
        await _require_admin(user_id)
        store = get_store()
        if hasattr(store, "backup_now"):
            path = await store.backup_now(time.strftime("%Y%m%d-%H%M%S"))
            audit("export", user_id, {"path": str(path)})
            return {"ok": True, "file": str(path),
                    "note": "已落盘（含密码哈希，勿外发）"}
        return {"ok": True, "file": str(ROLES_FILE),
                "note": "文件模式：roles.json 即备份"}

    @app.get("/roles/health")
    async def roles_health(
        user_id: str = Depends(get_current_user_id),
    ) -> dict:
        """健康自检（9.9-9）：连通性 / AOF / 冷备时间 / 补偿队列长度。"""
        await _require_admin(user_id)
        return await get_store().health()


async def _require_admin(user_id: str | None) -> None:
    """管理接口准入：统一按用户表 role 字段判定（入参为不可变 id）。

    取代旧的 ``user_id != "admin"`` 名字硬编码——否则第二个 admin
    账号会「页面进得去、API 全 403」。
    """
    if await get_user_role(user_id) != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
