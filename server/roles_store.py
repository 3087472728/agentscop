# -*- coding: utf-8 -*-
# 模块: roles_store.py —— 用户表存储访问层（rbac-fix-plan.md 第九章 9.3）
# users/user_agents/admin_edit 唯一事实源迁入 Redis；config/roles.json
# 退为「首启引导种子 + 可导出备份」。
#
# 双实现：RedisRolesStore（现役）+ FileRolesStore（AGENTS_ROLES_STORE=file
# 回滚开关）。键族（HGETALL 仅限 load_all 重建与 export——big-key 约束）：
#   roles:users  HASH field=<id> value=JSON{id,name,role,team,password}
#   roles:name   HASH field=<登录名> value=<id>
#   roles:user_agents  HASH field=<id> value=JSON[agent_id,...]
#   roles:config HASH admin_edit="true"/"false"
#   roles:ver    STRING 自增（缓存失效 + CAS）
#   roles:pending_disk LIST JSON{op,old,new}（磁盘补偿队列）
# 降级链（9.6）：Redis → 内存缓存 → config/roles.snapshot.json → fail closed
"""用户表存储访问层：对上暴露与旧 roles.json 同构的 dict（users 以 id
为键、每项含 name），对下屏蔽 Redis 键族、CAS 与降级细节。"""
import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("roles_store")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
ROLES_FILE = CONFIG_DIR / "roles.json"                # 首启种子
SNAPSHOT_FILE = CONFIG_DIR / "roles.snapshot.json"    # 降级快照
USER_ID_MAP_FILE = CONFIG_DIR / "user_id_map.json"    # 旧名→id 留档
BACKUP_DIR = CONFIG_DIR / "role_backups"              # 每日冷备
AUDIT_FILE = CONFIG_DIR / "roles_audit.log"
BACKUP_KEEP = 30

STORE_MODE = os.getenv("AGENTS_ROLES_STORE", "redis").strip().lower()
REQUIRE_AOF = os.getenv("AGENTS_REQUIRE_AOF", "1").strip().lower() not in ("0", "false", "no")

_VER_KEY, _USERS_KEY = "roles:ver", "roles:users"
_NAME_KEY, _UA_KEY = "roles:name", "roles:user_agents"
_CFG_KEY, _PENDING_KEY = "roles:config", "roles:pending_disk"
_PENDING_RETRY_INTERVAL = 300.0
_BACKUP_INTERVAL = 86400.0


class StoreConflict(Exception):
    """CAS 版本冲突：他人已先保存（HTTP 409）。"""


class StoreUnavailable(Exception):
    """存储不可用：降级窗口禁写 / AOF 未开启（HTTP 503）。"""


# --- id 生成（8.2：admin 字面量豁免，base58 无易混字符） --------------------
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58(data: bytes, length: int) -> str:
    n = int.from_bytes(data, "big")
    out: list[str] = []
    while n and len(out) < length:
        n, rem = divmod(n, 58)
        out.append(_B58[rem])
    while len(out) < length:
        out.append(_B58[0])
    return "".join(reversed(out))


def derive_id_from_name(name: str) -> str:
    """从登录名确定性派生 id（迁移/文件模式专用，重跑幂等）。"""
    return "u_" + _b58(hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest(), 10)


def new_user_id() -> str:
    """随机新用户 id："u_" + base58(8 字节熵)[:10]。"""
    return "u_" + _b58(secrets.token_bytes(8), 10)


def is_uid(value: str) -> bool:
    return value == "admin" or value.startswith("u_")


def atomic_write_json(path: Path, payload: dict) -> None:
    """tmp + os.replace 原子写 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def audit(action: str, actor: str, detail: dict) -> None:
    """追加审计日志（谁、何时、改了什么；失败不影响业务）。"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "actor": actor, "action": action, "detail": detail},
                          ensure_ascii=False)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        logger.warning("audit log write failed: %s", exc)


# --- 形态互转 ---------------------------------------------------------------
def empty_data() -> dict:
    return {"admin_edit": True, "users": {}, "user_agents": {}}


def _hashes_to_data(users_h, name_h, ua_h, cfg_h, ver) -> dict:
    users = {}
    for uid, raw in users_h.items():
        try:
            info = json.loads(raw)
        except (TypeError, ValueError):
            continue
        info.setdefault("id", uid)
        info.setdefault("name", name_h.get(uid, uid))
        users[uid] = info
    ua = {}
    for uid, raw in ua_h.items():
        try:
            ua[uid] = json.loads(raw)
        except (TypeError, ValueError):
            ua[uid] = []
    return {"admin_edit": (cfg_h.get("admin_edit", "true") == "true"),
            "users": users, "user_agents": ua, "_ver": ver}


def data_to_hashes(data: dict):
    users_h, name_h = {}, {}
    for uid, info in (data.get("users") or {}).items():
        if isinstance(info, dict):
            users_h[uid] = json.dumps(info, ensure_ascii=False)
            name_h[info.get("name") or uid] = uid
    ua_h = {uid: json.dumps(v, ensure_ascii=False)
            for uid, v in (data.get("user_agents") or {}).items()}
    cfg_h = {"admin_edit": "true" if data.get("admin_edit", True) else "false"}
    return users_h, name_h, ua_h, cfg_h


def to_name_keyed(data: dict, keep_password: bool = False) -> dict:
    """内部形态（id 键）→ 外部契约（登录名键）。

    GET /roles 响应与旧 roles.json 同形（默认去密码）——前端零改动；
    导出/冷备用 keep_password=True 保留哈希（回滚包必须含哈希）。
    """
    out = {"admin_edit": data.get("admin_edit", True), "users": {}}
    id2name = {uid: info.get("name") or uid for uid, info in (data.get("users") or {}).items()}
    for uid, info in (data.get("users") or {}).items():
        record = {"role": info.get("role", "member")}
        if info.get("team"):
            record["team"] = info["team"]
        if keep_password and info.get("password"):
            record["password"] = info["password"]
        elif not keep_password:
            record["password"] = ""
        out["users"][id2name[uid]] = record
    if data.get("user_agents"):
        out["user_agents"] = {id2name.get(uid, uid): v
                              for uid, v in data["user_agents"].items()}
    return out


# --- RedisRolesStore（现役） -------------------------------------------------
class RedisRolesStore:
    """roles:* 键族访问层：版本号轮询缓存 + WATCH CAS + 降级链。"""

    def __init__(self, storage=None) -> None:
        self._storage = storage            # 复用框架 RedisStorage 连接池
        self._cache: dict | None = None    # {"data":…, "ver":…}
        self._degraded = False
        self._aof_enabled: bool | None = None
        self._pending_count = 0
        self._last_backup_at: str | None = None
        self._tasks: list[asyncio.Task] = []

    def _client(self):
        client = self._storage.get_client() if self._storage else None
        if client is None:
            raise StoreUnavailable("Redis 连接尚未建立（应用启动中）")
        return client

    def _require_writable(self) -> None:
        """写前置检查：降级禁写；AOF 未开拒绝写（9.9-1 fail closed）。"""
        if self._degraded:
            raise StoreUnavailable("配置库暂不可用（降级只读窗口）")
        if REQUIRE_AOF and self._aof_enabled is False:
            raise StoreUnavailable("Redis 未开启 AOF 持久化，拒绝写入用户数据"
                                   "（开发环境可设 AGENTS_REQUIRE_AOF=0 解除）")

    async def _ping(self) -> bool:
        try:
            await self._client().ping()
            return True
        except Exception:
            return False

    @property
    def degraded(self) -> bool:
        """是否处于只读降级窗口（9.6：auth 用它判定 503 vs 401）。"""
        return self._degraded

    # -- 读路径（鉴权每请求 1 次 GET roles:ver） -----------------------------
    async def load_all(self) -> dict:
        """同构 dict；降级链 Redis → 内存缓存 → 本地快照 → 空表。"""
        try:
            ver = int(await self._client().get(_VER_KEY) or 0)
        except StoreUnavailable:
            raise
        except Exception:
            ver = None
        if ver is not None:
            if self._degraded:
                logger.info("Redis 恢复，退出降级模式")
                self._degraded = False
            if self._cache and self._cache["ver"] == ver:
                return self._cache["data"]
            try:
                pipe = self._client().pipeline(transaction=False)
                pipe.hgetall(_USERS_KEY)
                pipe.hgetall(_NAME_KEY)
                pipe.hgetall(_UA_KEY)
                pipe.hgetall(_CFG_KEY)
                users_h, name_h, ua_h, cfg_h = await pipe.execute()
                data = _hashes_to_data(users_h, name_h, ua_h, cfg_h, ver)
            except Exception:
                data = None
            if data is not None:
                self._cache = {"data": data, "ver": ver}
                self._write_snapshot(data)
                return data
        self._degraded = True
        if self._cache:
            return self._cache["data"]
        snap = self._read_snapshot()
        if snap is not None:
            logger.warning("Redis 不可达且无内存缓存，使用本地快照（只读降级）")
            return snap
        logger.error("Redis 不可达且无任何缓存/快照，返回空用户表（fail closed）")
        return empty_data()

    def _read_snapshot(self) -> dict | None:
        try:
            data = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
        data.pop("_ver", None)
        data.pop("_saved_at", None)
        return data

    def _write_snapshot(self, data: dict) -> None:
        try:
            payload = {k: v for k, v in data.items() if not k.startswith("_")}
            payload["_saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            atomic_write_json(SNAPSHOT_FILE, payload)
        except OSError as exc:
            logger.warning("snapshot write failed: %s", exc)

    # -- 写路径（CAS：WATCH roles:ver → 重建 → INCR ver） ---------------------
    async def save_all(self, data: dict, expected_ver: int, actor: str = "?") -> int:
        """整表保存（管理页语义）；版本被抢先 → StoreConflict。"""
        self._require_writable()
        from redis.exceptions import WatchError

        users_h, name_h, ua_h, cfg_h = data_to_hashes(data)
        client = self._client()
        pipe = client.pipeline(transaction=True)
        try:
            async with pipe as p:
                await p.watch(_VER_KEY)
                cur = int(await p.get(_VER_KEY) or 0)
                if cur != expected_ver:
                    await p.unwatch()
                    raise StoreConflict(f"expected ver {expected_ver}, got {cur}")
                p.multi()
                p.delete(_USERS_KEY, _NAME_KEY, _UA_KEY, _CFG_KEY)
                if users_h:
                    p.hset(_USERS_KEY, mapping=users_h)
                if name_h:
                    p.hset(_NAME_KEY, mapping=name_h)
                if ua_h:
                    p.hset(_UA_KEY, mapping=ua_h)
                p.hset(_CFG_KEY, mapping=cfg_h)
                p.set(_VER_KEY, cur + 1)
                await p.execute()
        except WatchError as exc:
            raise StoreConflict("版本被其他保存抢先") from exc
        self._cache = None
        audit("save_all", actor, {"ver": cur + 1})
        return cur + 1

    async def upsert_user(self, uid: str, info: dict, actor: str = "system") -> None:
        """单用户写入（种子导入/引导路径）。"""
        self._require_writable()
        client = self._client()
        ver = int(await client.get(_VER_KEY) or 0)
        pipe = client.pipeline(transaction=True)
        pipe.hset(_USERS_KEY, uid, json.dumps(info, ensure_ascii=False))
        pipe.hset(_NAME_KEY, info.get("name") or uid, uid)
        pipe.set(_VER_KEY, ver + 1)
        await pipe.execute()
        self._cache = None
        audit("upsert_user", actor, {"id": uid, "name": info.get("name")})

    async def delete_user(self, uid: str, actor: str = "system") -> None:
        self._require_writable()
        client = self._client()
        name = None
        raw = await client.hget(_USERS_KEY, uid)
        if raw:
            try:
                name = json.loads(raw).get("name")
            except ValueError:
                pass
        ver = int(await client.get(_VER_KEY) or 0)
        pipe = client.pipeline(transaction=True)
        pipe.hdel(_USERS_KEY, uid)
        if name:
            pipe.hdel(_NAME_KEY, name)
        pipe.hdel(_UA_KEY, uid)
        pipe.set(_VER_KEY, ver + 1)
        await pipe.execute()
        self._cache = None
        audit("delete_user", actor, {"id": uid, "name": name})

    async def resolve(self, name_or_id: str | None) -> str | None:
        """登录名 → id（单点 HGET roles:name）；id 直接通过。

        降级窗口用内存/快照缓存反查（鉴权必须继续可用，9.6）。
        """
        if not name_or_id:
            return None
        value = name_or_id.strip()
        if not self._degraded:
            try:
                uid = await self._client().hget(_NAME_KEY, value)
                if uid:
                    return uid
            except Exception:
                self._degraded = True
            return value if is_uid(value) else None
        data = await self.load_all()
        for uid, info in (data.get("users") or {}).items():
            if value in (uid, info.get("name")):
                return uid
        return None

    async def rename_user(self, uid: str, new_name: str) -> None:
        """改名（9.7 P3.5）：id 不变 → 改 value.name + 维护索引（Lua 原子）。"""
        self._require_writable()
        script = (
            "local raw = redis.call('HGET', KEYS[1], ARGV[1]) "
            "if not raw then return {err='not_found'} end "
            "local info = cjson.decode(raw) "
            "local old = info.name "
            "info.name = ARGV[2] "
            "redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(info)) "
            "if old and old ~= ARGV[2] then redis.call('HDEL', KEYS[2], old) end "
            "redis.call('HSET', KEYS[2], ARGV[2], ARGV[1]) "
            "redis.call('INCR', KEYS[3]) return old or ''"
        )
        from redis.exceptions import ResponseError

        try:
            old = await self._client().eval(
                script, 3, _USERS_KEY, _NAME_KEY, _VER_KEY, uid, new_name)
        except ResponseError as exc:
            if "not_found" in str(exc):
                raise KeyError(uid) from exc
            raise
        if old is None:
            raise KeyError(uid)
        self._cache = None
        audit("rename_user", "admin", {"id": uid, "from": old, "to": new_name})

    # -- 导出 / 冷备（HGETALL 仅此处与导入——big-key 约束） --------------------
    async def export_data(self) -> dict:
        """从 Redis 重建同构 dict（登录名键 + 保留密码哈希）。"""
        pipe = self._client().pipeline(transaction=False)
        pipe.hgetall(_USERS_KEY)
        pipe.hgetall(_NAME_KEY)
        pipe.hgetall(_UA_KEY)
        pipe.hgetall(_CFG_KEY)
        users_h, name_h, ua_h, cfg_h = await pipe.execute()
        data = _hashes_to_data(users_h, name_h, ua_h, cfg_h, 0)
        data.pop("_ver")
        return to_name_keyed(data, keep_password=True)

    async def backup_now(self, tag: str | None = None) -> Path:
        """立即落一份冷备（每日任务与运维共用；冷备即回滚包，9.9-2/10）。"""
        data = await self.export_data()
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUP_DIR / f"roles.backup-{tag or time.strftime('%Y%m%d')}.json"
        atomic_write_json(path, data)
        self._last_backup_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        _rotate_backups()
        audit("backup", "system", {"path": str(path)})
        return path

    # -- 磁盘补偿队列（9.9-3/4：跨存储缺口自动收敛） --------------------------
    async def push_pending_disk(self, op: dict) -> None:
        await self._client().rpush(_PENDING_KEY, json.dumps(op, ensure_ascii=False))
        self._pending_count += 1

    async def _pending_items(self) -> list[dict]:
        raw = await self._client().lrange(_PENDING_KEY, 0, -1)
        out = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except ValueError:
                continue
        self._pending_count = len(out)
        return out

    async def _retry_pending(self) -> int:
        """幂等重试：目标目录已在 → 成功；源不在 → 无事；成功项 LREM 出队。"""
        from .per_user_workspace import safe_user_component

        done = 0
        for item in await self._pending_items():
            old_c, new_c = safe_user_component(item["old"]), safe_user_component(item["new"])
            ok = True
            root = Path(__file__).resolve().parent.parent / "workspaces"
            if root.is_dir():
                for agent_dir in sorted(root.iterdir()):
                    src, dst = agent_dir / "users" / old_c, agent_dir / "users" / new_c
                    if dst.exists() or not src.exists():
                        continue
                    try:
                        src.rename(dst)
                    except OSError as exc:
                        ok = False
                        logger.warning("pending retry failed: %s -> %s: %s", src, dst, exc)
            if ok:
                await self._client().lrem(_PENDING_KEY, 1, json.dumps(item, ensure_ascii=False))
                done += 1
                audit("pending_disk_done", "system", item)
        return done

    # -- 健康与启动 -----------------------------------------------------------
    async def health(self) -> dict:
        reachable = await self._ping()
        return {"mode": "redis", "reachable": reachable, "degraded": self._degraded,
                "aof_enabled": self._aof_enabled,
                "pending_disk_ops": self._pending_count,
                "last_backup_at": self._last_backup_at,
                "snapshot_file": str(SNAPSHOT_FILE), "audit_file": str(AUDIT_FILE)}

    async def startup(self) -> None:
        """启动自检序列（9.9）：PING → AOF → pending → 种子导入 → 快照预热。"""
        if not await self._ping():
            self._degraded = True
            logger.warning("Redis PING 失败：进入只读降级（内存缓存/本地快照兜底）")
        else:
            info = await self._client().info("persistence")
            self._aof_enabled = bool(info.get("aof_enabled", 0))
            if not self._aof_enabled:
                logger.warning("⚠️ Redis 未开启 AOF 持久化（appendonly yes）——重启将"
                               "丢失全部用户数据；已拒绝用户写操作，请修复部署配置。")
            try:
                await self._retry_pending()
            except Exception as exc:
                logger.warning("启动期补偿队列重试失败: %s", exc)
        await self._seed_if_empty()
        try:
            await self.load_all()
        except StoreUnavailable:
            pass
        self._tasks = [asyncio.create_task(self._loop(self._retry_pending, _PENDING_RETRY_INTERVAL)),
                       asyncio.create_task(self._loop(self._backup_tick, _BACKUP_INTERVAL))]

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self, coro_fn, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await coro_fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("background task %s failed: %s", coro_fn, exc)

    async def _backup_tick(self) -> None:
        if await self._ping():
            await self.backup_now(time.strftime("%Y%m%d"))

    async def _seed_if_empty(self) -> None:
        """首启导入（9.5）：Redis 空 → roles.json 种子 / .env 引导。"""
        if self._degraded or await self._client().hlen(_USERS_KEY):
            return
        if ROLES_FILE.is_file():
            try:
                seed = json.loads(ROLES_FILE.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.error("种子文件 %s 损坏，跳过导入: %s", ROLES_FILE, exc)
                return
            id_map = {}
            for name, info in (seed.get("users") or {}).items():
                if not isinstance(info, dict):
                    continue
                uid = "admin" if name == "admin" else derive_id_from_name(name)
                record = {"id": uid, "name": name,
                          "role": info.get("role", "member")}
                if info.get("team"):
                    record["team"] = info["team"]
                if info.get("password"):
                    record["password"] = info["password"]
                await self.upsert_user(uid, record, actor="seed_import")
                id_map[name] = uid
            # 智能体授权（user_agents：登录名键 → id 键）同步迁入，
            # 否则种子导入后全员授权清空（可见性回归为默认）
            ua_seed: dict[str, list] = {}
            for name, allow in (seed.get("user_agents") or {}).items():
                uid = "admin" if name == "admin" else id_map.get(name)
                if uid and isinstance(allow, list):
                    ua_seed[uid] = allow
            if ua_seed:
                pipe = self._client().pipeline(transaction=True)
                pipe.hset(_UA_KEY, mapping={
                    u: json.dumps(v, ensure_ascii=False)
                    for u, v in ua_seed.items()})
                pipe.incr(_VER_KEY)
                await pipe.execute()
                self._cache = None
                logger.info("已迁移 %d 个用户的智能体授权", len(ua_seed))
            atomic_write_json(USER_ID_MAP_FILE, id_map)
            archive = ROLES_FILE.with_name(
                f"roles.json.imported-{time.strftime('%Y%m%d-%H%M%S')}")
            os.replace(ROLES_FILE, archive)
            logger.info("已从 roles.json 导入 %d 个用户（映射 %s，原文件归档 %s）",
                        len(id_map), USER_ID_MAP_FILE, archive)
            return
        # 空库且无种子 → 旧式 .env 引导（原 bootstrap 逻辑平移）
        from .auth import hash_password, load_env_file, parse_users

        load_env_file()
        legacy = parse_users(os.environ.get("AGENTS_USERS", ""))
        if not legacy:
            logger.warning("Redis 无用户且无种子/.env 来源：用户表为空")
            return
        for name, password in legacy.items():
            uid = "admin" if name == "admin" else new_user_id()
            await self.upsert_user(
                uid, {"id": uid, "name": name,
                      "role": "admin" if name == "admin" else "member",
                      "password": hash_password(password)},
                actor="bootstrap")
        logger.info("已从 .env AGENTS_USERS 引导 %d 个账号入 Redis", len(legacy))


def _rotate_backups() -> None:
    """冷备轮转：仅保留最近 BACKUP_KEEP 份。"""
    try:
        for stale in sorted(BACKUP_DIR.glob("roles.backup-*.json"))[:-BACKUP_KEEP]:
            stale.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("backup rotation failed: %s", exc)


@contextmanager
def file_lock(timeout: float = 10.0, stale: float = 30.0):
    """roles.json 写锁（O_CREAT|O_EXCL 独占 + 超时 + 崩溃残留过期兜底）。

    与旧 roles_admin_api._roles_lock 同语义；FileRolesStore 的
    save/rename 全程持有，杜绝双管理员互覆盖。
    """
    lock_path = Path(str(ROLES_FILE) + ".lock")
    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > stale:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise StoreConflict(f"另一管理员正在保存（等待锁超过 {timeout:.0f}s）")
            time.sleep(0.1)
    try:
        os.write(fd, str(os.getpid()).encode())
    finally:
        os.close(fd)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


# --- FileRolesStore（回滚开关：保留旧 roles.json 全部语义） -------------------
class FileRolesStore:
    """旧文件模式同接口实现。``_ver`` 取文件内容摘要供 CAS。"""

    def __init__(self) -> None:
        self._aof_enabled = None
        self._degraded = False
        self._pending_count = 0
        self._last_backup_at = None

    @property
    def degraded(self) -> bool:
        return self._degraded

    def _read_raw(self) -> dict:
        if not ROLES_FILE.is_file():
            from .auth import bootstrap_roles_file

            bootstrap_roles_file()
        try:
            return json.loads(ROLES_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"admin_edit": True, "users": {}}

    @staticmethod
    def _ver_of(raw: dict) -> int:
        blob = json.dumps(raw, sort_keys=True, ensure_ascii=False)
        return int(hashlib.blake2b(blob.encode(), digest_size=8).hexdigest()[:12], 16)

    @staticmethod
    def _to_internal(raw: dict) -> dict:
        """文件形态（登录名键）→ 内部形态（id 键，确定性派生）。"""
        users = {}
        for name, info in (raw.get("users") or {}).items():
            uid = "admin" if name == "admin" else derive_id_from_name(name)
            users[uid] = {"id": uid, "name": name,
                          "role": (info or {}).get("role", "member")}
            if (info or {}).get("team"):
                users[uid]["team"] = info["team"]
            if (info or {}).get("password"):
                users[uid]["password"] = info["password"]
        ua = {}
        for name, allow in (raw.get("user_agents") or {}).items():
            ua["admin" if name == "admin" else derive_id_from_name(name)] = allow
        return {"admin_edit": raw.get("admin_edit", True),
                "users": users, "user_agents": ua, "_ver": FileRolesStore._ver_of(raw)}

    @staticmethod
    def _to_file(data: dict) -> dict:
        out = {"admin_edit": data.get("admin_edit", True), "users": {}}
        for info in (data.get("users") or {}).values():
            record = {"role": info.get("role", "member")}
            if info.get("team"):
                record["team"] = info["team"]
            if info.get("password"):
                record["password"] = info["password"]
            out["users"][info.get("name") or info["id"]] = record
        if data.get("user_agents"):
            id2name = {uid: i.get("name") or uid
                       for uid, i in (data.get("users") or {}).items()}
            out["user_agents"] = {id2name.get(uid, uid): v
                                  for uid, v in data["user_agents"].items()}
        return out

    async def load_all(self) -> dict:
        return self._to_internal(self._read_raw())

    async def save_all(self, data: dict, expected_ver: int, actor: str = "?") -> int:
        raw = self._read_raw()
        if self._ver_of(raw) != expected_ver:
            raise StoreConflict("文件已被其他保存修改")
        with file_lock():
            atomic_write_json(ROLES_FILE, self._to_file(data))
        audit("save_all(file)", actor, {})
        return self._ver_of(self._read_raw())

    async def resolve(self, name_or_id: str | None) -> str | None:
        if not name_or_id:
            return None
        value = name_or_id.strip()
        users = self._to_internal(self._read_raw())["users"]
        for uid, info in users.items():
            if value in (uid, info.get("name")):
                return uid
        return None

    async def rename_user(self, uid: str, new_name: str) -> None:
        data = await self.load_all()
        info = data["users"].get(uid)
        if not info:
            raise KeyError(uid)
        old = info["name"]
        del data["users"][uid]
        data["users"][uid] = {**info, "name": new_name}
        raw = self._read_raw()
        users_file = raw.get("users") or {}
        if old in users_file:
            moved = users_file.pop(old)
            moved = {**moved, "role": info.get("role", "member")}
            if info.get("team"):
                moved["team"] = info["team"]
            users_file[new_name] = moved
        ua = raw.get("user_agents") or {}
        if old in ua:
            ua[new_name] = ua.pop(old)
        raw["users"], raw["user_agents"] = users_file, ua
        with file_lock():
            atomic_write_json(ROLES_FILE, raw)
        audit("rename_user(file)", "admin", {"id": uid, "from": old, "to": new_name})

    async def health(self) -> dict:
        return {"mode": "file", "reachable": True, "degraded": False,
                "aof_enabled": None, "pending_disk_ops": 0,
                "last_backup_at": None, "roles_file": str(ROLES_FILE)}


# --- 单例与装配 ---------------------------------------------------------------
_store: RedisRolesStore | FileRolesStore | None = None


def bind_storage(storage) -> RedisRolesStore:
    """main.py 启动时装配：绑定框架 RedisStorage 复用连接池。"""
    global _store
    if STORE_MODE == "file":
        _store = FileRolesStore()
    else:
        _store = RedisRolesStore(storage)
    return _store


def get_store() -> RedisRolesStore | FileRolesStore:
    """全局访问点；未装配时回落文件模式（脚本/单测场景）。"""
    global _store
    if _store is None:
        _store = FileRolesStore()
    return _store
