# -*- coding: utf-8 -*-
"""agentscop-platform 各脚本共用的辅助函数（仅用标准库）。

认证模型（RBAC 收敛后）：
- 用户清单读 ``config/roles.json`` 的 ``users`` 键（唯一事实源，
  旧 .env 的 AGENTS_USERS 已废弃）；
- 脚本一律以 **admin** 身份做 Basic 认证：密码取环境变量
  ``AGENTSCOPE_PASSWORD``（或 .env 同名键），缺失时 getpass 交互输入；
- 需要以某普通用户身份调 API 时（可见性矩阵、批量发凭据），附带
  ``X-On-Behalf-Of: <user>`` 头由后端代操作（要求 admin + admin_edit）。
  密码哈希化后任何人都拿不到明文，这是逐用户身份场景的唯一通路。

环境变量覆盖项：
- AGENTSCOPE_API             后端基础地址   （默认 http://localhost:<端口>，
                              端口取 AGENTS_BACKEND_PORT，本地默认 8020，
                              与 start-dev/start-all 一致；容器/服务器为 8000）
- AGENTSCOPE_USER            管理员用户名   （默认 "admin"）
- AGENTSCOPE_PASSWORD        管理员密码     （缺失时 getpass 交互输入）
- AGENTSCOPE_WORKSPACES_DIR  工作区根目录   （默认 <agentscop-platform>/workspaces）
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
EXTRA_ROOT = SCRIPTS_DIR.parent
WORKSPACES_DIR = Path(
    os.environ.get(
        "AGENTSCOPE_WORKSPACES_DIR",
        str(EXTRA_ROOT / "workspaces"),
    )
)
AGENTS_DIR = EXTRA_ROOT / "agents"
SKILLS_DIR = EXTRA_ROOT / "skills"
REGISTRY_FILE = EXTRA_ROOT / "registry.json"
ENV_FILE = EXTRA_ROOT / ".env"
#: 用户/角色表（与 server/auth.py 共用同一份配置，唯一事实源）
ROLES_FILE = EXTRA_ROOT / "config" / "roles.json"

# 与服务端（server/paths.py）同规则：AGENTS_BACKEND_PORT 覆盖，
# 本地脚本默认 8020（8000 常被本机 IDE 的本地服务占用）。
_backend_port = os.environ.get("AGENTS_BACKEND_PORT", "8020").strip()
BASE_URL = os.environ.get(
    "AGENTSCOPE_API", f"http://localhost:{_backend_port}"
).rstrip("/")
USER_ID = os.environ.get("AGENTSCOPE_USER", "admin")


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_ensure_utf8_stdout()


def load_env() -> dict:
    """把 EXTRA_ROOT/.env 解析成字典，不改动 os.environ。"""
    values = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_roles() -> dict:
    """读取 config/roles.json（用户/角色表唯一事实源）。"""
    if not ROLES_FILE.is_file():
        raise RuntimeError(
            f"{ROLES_FILE} 不存在 —— 请先启动一次后端（会自动引导生成），"
            "或用 AGENTSCOPE_API 指向已部署的后端。"
        )
    return json.loads(ROLES_FILE.read_text(encoding="utf-8"))


def agents_users() -> dict[str, dict]:
    """从 roles.json 读取用户表（仅用户清单与角色，不含密码）。

    旧 .env 的 ``AGENTS_USERS`` 已废弃——密码哈希化后配置文件里
    本来也不再有可用密码。返回 ``{user: {"role": ..., ...}}``。
    """
    return (read_roles().get("users") or {})


def parse_users(raw: str) -> dict[str, str]:
    """把 ``AGENTS_USERS='admin:pw1,u2:pw2'`` 解析成字典。

    .. deprecated:: 仅 _auth_probe.py 的探针兼容路径还在引用；
       用户表事实源已迁移至 roles.json（见 :func:`agents_users`）。
    """
    users: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        user, _, password = entry.partition(":")
        if user.strip() and password:
            users[user.strip()] = password
    return users


def http_ssl_context():
    """TLS context for API calls against AGENTSCOPE_API.

    Self-signed deployments set AGENTS_INSECURE_TLS=1 (.env or env var)
    so admin scripts skip certificate verification. Unset keeps the
    default strict verification (http URLs ignore the context).
    """
    flag = (
        os.environ.get("AGENTS_INSECURE_TLS")
        or load_env().get("AGENTS_INSECURE_TLS", "")
    )
    if flag.strip().lower() in ("1", "true", "yes", "on"):
        import ssl

        return ssl._create_unverified_context()
    return None


_ADMIN_PASSWORD: str | None = None


def admin_password() -> str:
    """获取管理员密码：AGENTSCOPE_PASSWORD（env/.env）→ getpass 交互。

    进程内缓存一次（交互输入不应重复询问）。密码哈希化后脚本层
    无法从任何配置文件还原，只能由运维侧提供。
    """
    global _ADMIN_PASSWORD
    if _ADMIN_PASSWORD:
        return _ADMIN_PASSWORD
    pw = (
        os.environ.get("AGENTSCOPE_PASSWORD", "").strip()
        or load_env().get("AGENTSCOPE_PASSWORD", "").strip()
    )
    if not pw:
        import getpass

        pw = getpass.getpass(f"Password for admin user '{USER_ID}': ")
    if not pw:
        raise RuntimeError("Admin password is required (set AGENTSCOPE_PASSWORD).")
    _ADMIN_PASSWORD = pw
    return pw


def auth_headers(on_behalf_of: str | None = None) -> dict:
    """构造 API 请求头：admin Basic 认证（主身份）。

    ``on_behalf_of`` 提供时附带 ``X-On-Behalf-Of`` 头，以该普通用户
    身份执行本次调用（后端要求 admin + admin_edit）。被代用户也
    必须存在于 roles.json，否则后端按未在册处理。

    roles.json 尚不存在（后端从未启动）时退回朴素 X-User-ID——
    与认证未启用的部署兼容。
    """
    headers: dict = {}
    try:
        users = agents_users()
    except RuntimeError:
        users = {}
    if not users:
        headers["X-User-ID"] = on_behalf_of or USER_ID
        return headers

    import base64

    token = base64.b64encode(
        f"{USER_ID}:{admin_password()}".encode("utf-8")
    ).decode("ascii")
    headers["X-User-ID"] = USER_ID
    headers["Authorization"] = f"Basic {token}"
    if on_behalf_of and on_behalf_of != USER_ID:
        headers["X-On-Behalf-Of"] = on_behalf_of
    return headers


def api(
    method: str,
    path: str,
    body: dict | None = None,
    timeout: int = 60,
    user: str | None = None,
):
    """携带认证头调用 AgentScope 后端。

    ``user``：以该用户身份执行（X-On-Behalf-Of 代操作，用于多用户
    可见性验证 / 批量发凭据等场景）；传 None 或管理员自身时以 admin
    主身份调用。返回 (状态码, 解析后的JSON或None)。HTTP 出错时抛
    RuntimeError，消息中带响应 detail。
    """
    url = f"{BASE_URL}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method.upper(),
        headers={
            **auth_headers(user),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=http_ssl_context()
        ) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail[:800]}") from exc


def safe_component(user_id: str) -> str:
    """镜像后端的 用户id→路径组件 净化逻辑。"""
    import re

    sanitized = re.sub(r"[^A-Za-z0-9_\-]", "_", str(user_id))[:64]
    return sanitized or "_anonymous"


def skill_name_of(skill_dir: Path) -> str:
    """从技能的 SKILL.md 中提取 frontmatter 的 ``name`` 字段。"""
    md = skill_dir / "SKILL.md"
    text = md.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        raise ValueError(f"{md}: missing YAML frontmatter block")
    name_match = re.search(r"^name:\s*(.+?)\s*$", match.group(1), re.MULTILINE)
    if not name_match or not name_match.group(1):
        raise ValueError(f"{md}: frontmatter missing required 'name' field")
    return name_match.group(1).strip()


def discover_skills(base: Path) -> list[Path]:
    """返回 ``base`` 下直接含有 SKILL.md 的子目录列表。"""
    if not base.is_dir():
        return []
    return sorted(
        d for d in base.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()
    )


def load_registry() -> dict:
    if REGISTRY_FILE.is_file():
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return {}


def save_registry(registry: dict) -> None:
    REGISTRY_FILE.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
