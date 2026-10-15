# -*- coding: utf-8 -*-
"""AgentScope 应用的 HTTP Basic 认证。

框架默认通过朴素的 ``X-User-ID`` 请求头解析调用者身份
（``agentscope.app.deps.get_current_user_id``）——在可信内网尚可，
对外则形同虚设。本模块在组装层加固，不改动框架本身：

- 用户表唯一事实源是 ``roles_store``（Redis ``roles:*`` 键族；
  ``AGENTS_ROLES_STORE=file`` 时回滚到 ``config/roles.json`` 文件模式，
  见 rbac-fix-plan.md 第九章）。用户以不可变 id 为主键，登录名仅作
  登录/展示（Basic 用户名经 ``roles:name`` 索引翻译为 id 返回）；
  ``users.<id>.password`` 保存加盐 PBKDF2-SHA256 哈希
  （``sha256$<salt_hex>$<hash_hex>``）——绝不存明文；
- :func:`install_basic_auth` 通过 FastAPI 的
  ``app.dependency_overrides`` 替换框架的身份依赖——所有声明了
  ``Depends(get_current_user_id)`` 的端点从此都要求
  ``Authorization: Basic base64(user:password)``，并将校验通过的
  用户 id 作为身份返回（Basic 用户名可以是登录名，也可以直接是 id）；
- 首次启动时 Redis 无用户数据 → 从 ``config/roles.json`` 种子导入；
  无种子文件时退回旧式 ``.env`` 的 ``AGENTS_USERS=admin:pw1,u2:pw2``
  引导（明文密码在导入时即被哈希）；
- 存储值若不带 ``sha256$`` 前缀，仍按明文比较接受——这是为回滚
  窗口保留的兼容分支；
- 用户表为空则禁用校验（退回请求头行为）；降级且无任何缓存/快照
  时返回 503（fail closed，9.6）。

浏览器原生下载走的是短时效能力令牌（无法携带请求头），前端 SSE
使用基于 fetch 的流式请求，因此所有客户端路径都能发送该请求头。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request, status

from .roles_store import get_store

#: agentscope_extra/.env —— 引导凭证来源（旧式）。
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

#: agentscope_extra/config/roles.json —— 首启种子 / 文件模式数据源。
ROLES_FILE = Path(__file__).resolve().parent.parent / "config" / "roles.json"

#: PBKDF2 迭代次数（OWASP 2023 对 PBKDF2-SHA256 的建议值）。
_PBKDF2_ITERATIONS = 100_000

_HASH_SCHEME = "sha256"


def load_env_file() -> None:
    """把项目 .env 读入 os.environ（先写入者生效）。"""
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# 密码哈希（PBKDF2-SHA256，每用户独立随机盐，仅用标准库）
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """把明文密码哈希为 ``sha256$<salt_hex>$<hash_hex>`` 格式。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plain.encode("utf-8"), salt, _PBKDF2_ITERATIONS,
    )
    return f"{_HASH_SCHEME}${salt.hex()}${digest.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    """以恒定时间比较方式校验存储值。

    带 ``sha256$`` 前缀的值走 PBKDF2 校验；其余一律按明文比较
    （旧版兼容分支，见模块 docstring）。
    """
    if not stored:
        return False
    if not stored.startswith(f"{_HASH_SCHEME}$"):
        return hmac.compare_digest(plain, stored)
    try:
        _, salt_hex, hash_hex = stored.split("$", 2)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", plain.encode("utf-8"), salt, _PBKDF2_ITERATIONS,
    )
    return hmac.compare_digest(actual, expected)


def is_hashed(value: str) -> bool:
    """判断 ``value`` 是否已是哈希形式。"""
    return value.startswith(f"{_HASH_SCHEME}$")


# ---------------------------------------------------------------------------
# 用户表：config/roles.json（首次由旧式 .env 引导生成）
# ---------------------------------------------------------------------------

def parse_users(raw: str) -> dict[str, str]:
    """把 ``AGENTS_USERS='admin:pw1,u2:pw2'`` 解析成字典。"""
    users: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        user, _, password = entry.partition(":")
        if user.strip() and password:
            users[user.strip()] = password
    return users


def bootstrap_roles_file() -> None:
    """从旧式 ``AGENTS_USERS`` 生成 ``config/roles.json``。

    仅在首次启动（文件不存在）时运行。旧式明文密码在导入时即被
    哈希；``admin`` 账号获得 admin 角色，其余默认 member。文件已
    存在或未配置旧式用户时不做任何事。
    """
    if ROLES_FILE.is_file():
        return
    load_env_file()
    legacy = parse_users(os.environ.get("AGENTS_USERS", ""))
    if not legacy:
        return
    users: dict[str, dict] = {}
    for name, password in legacy.items():
        users[name] = {
            "role": "admin" if name == "admin" else "member",
            "password": hash_password(password),
        }
    payload = {"admin_edit": True, "users": users}
    ROLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROLES_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    os.replace(tmp, ROLES_FILE)


async def load_users() -> dict[str, str]:
    """读取用户表 → ``{id: stored_value}``（哈希或旧版明文）。

    经 ``roles_store`` 读（Redis 版本号轮询缓存 / 文件直读），
    改密码无需重启。
    """
    data = await get_store().load_all()
    users: dict[str, str] = {}
    for uid, info in (data.get("users") or {}).items():
        if isinstance(info, dict) and info.get("password"):
            users[uid] = str(info["password"])
    return users


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing credentials.",
        headers={"WWW-Authenticate": "Basic"},
    )


# ---------------------------------------------------------------------------
# 角色查询（store 的 role 字段是 admin 判定的唯一事实源）+ 代操作
# ---------------------------------------------------------------------------

async def load_roles_config() -> dict:
    """读取全量用户配置（store 版本号轮询缓存，热更新无需重启）。

    只读 users / admin_edit 等管理键；密码哈希不会被本函数外泄
    （外泄面收敛在 GET /roles 的置空逻辑）。
    """
    return await get_store().load_all()


async def get_user_role(user_id: str | None) -> str:
    """返回用户角色（``admin`` / ``leader`` / ``member``）。

    ``user_id`` 是不可变 id。不在册用户一律按最低权限 ``member``
    处理；user_id 为空同理。admin 判定统一走本函数——后端 API 与
    前端（经 GET /me）共用同一事实源，避免「名字硬编码」式双标准。
    """
    if not user_id:
        return "member"
    data = await load_roles_config()
    info = (data.get("users") or {}).get(user_id) or {}
    return info.get("role", "member")


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="X-On-Behalf-Of 仅限具备 admin_edit 权限的管理员使用",
    )


async def basic_auth_user(request: Request) -> str:
    """校验 ``Authorization: Basic`` 并返回用户 id。

    Basic 用户名可以是登录名（经 ``roles:name`` 索引翻译），也可以
    直接是 id。返回值统一为不可变 id——下游资源键 / 可见性 / 工作区
    目录全部以 id 归属。

    支持管理员代操作：请求头 ``X-On-Behalf-Of: <登录名或 id>`` ——
    仅当真实身份 role == "admin" 且 admin_edit: true 时生效，返回被
    代用户的 id。普通用户带上此头一律 403。

    特殊窗口（9.6 降级语义）：
    · 降级且无任何缓存/快照（空表）→ 503 fail closed；
    · 用户表为空且非降级（全新环境未引导）→ 退回 X-User-ID 请求头
      行为（保留旧「空表禁用校验」语义）。
    """
    store = get_store()
    data = await store.load_all()
    cfg_users = data.get("users") or {}
    if not cfg_users:
        if getattr(store, "degraded", False):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="配置库暂不可用（降级且无本地快照），请稍后重试",
            )
        # 空表且非降级：全新环境未引导——退回旧请求头行为
        return (request.headers.get("X-User-ID") or "").strip()

    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        raise _unauthorized()
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except Exception as exc:  # base64 格式错误 / 非 utf8 编码
        raise _unauthorized() from exc
    username, sep, password = decoded.partition(":")
    if not sep:
        raise _unauthorized()

    uid = await store.resolve(username)
    expected = cfg_users.get(uid, {}).get("password") if uid else None
    if not uid or not expected or not verify_password(password, expected):
        raise _unauthorized()

    behalf = (request.headers.get("X-On-Behalf-Of") or "").strip()
    if behalf:
        my = cfg_users.get(uid) or {}
        if my.get("role", "member") != "admin" or not data.get("admin_edit", True):
            raise _forbidden()
        target = await store.resolve(behalf)
        return target or behalf
    return uid


def install_basic_auth(app) -> None:
    """全应用范围内替换框架的身份依赖。

    无条件安装（store 是否有用户数据在请求期判定——Redis 尚未连接
    时无法在装配期得知）；空表语义见 :func:`basic_auth_user`。
    """
    from agentscope.app.deps import get_current_user_id

    app.dependency_overrides[get_current_user_id] = basic_auth_user
