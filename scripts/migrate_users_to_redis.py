# -*- coding: utf-8 -*-
"""一次性迁移（rbac-fix-plan.md 9.5）：登录名主键 → 不可变 id + 入 Redis。

四步，每步独立幂等、可 ``--step`` 单独执行（默认 ``all`` 顺序执行）：

  ids    生成/合并 ``config/user_id_map.json``（旧登录名 → id；
         admin 字面量豁免；id 派生与 roles_store/FileRolesStore 一致，
         保证迁移前后 / 回滚后同一登录名永远得到同一 id）
  redis  用户元数据入 Redis：config/roles.json → ``roles:users`` /
         ``roles:name`` / ``roles:user_agents`` / ``roles:config``，
         ``roles:ver`` 置 1（此后应用首启种子导入因库非空自动跳过）
  owner  SCAN ``agentscope:user:<旧名>:*`` → RENAME 为
         ``agentscope:user:<id>:*``（资源 owner 换键；源键已消失则跳过）
  disk   ``workspaces/<agent>/users/<旧名>`` → ``/<id 净化名>``；
         失败项 LPUSH 进 ``roles:pending_disk`` 补偿队列，由应用后台
         每 5 min 幂等重试自动收敛（9.9-3/4）

安全护栏（9.9-8）：
  · ``--dry-run`` 打印全部将执行的动作，零写入（Redis/roles.json/磁盘）；
  · 顺序守卫：``roles:users`` 非空时拒绝 redis 步骤——防「先入 Redis
    后换 id」的二次搬家（执行顺序颠倒直接拒绝）；
  · AOF 未开启时拒绝全部写入步骤（``--allow-no-aof`` 显式豁免，仅限
    一次性演练环境）；
  · 迁移后自动校验并输出报告：旧名 owner 键残留 = 0、磁盘旧目录
    遗留 = 0、``roles:name`` ↔ ``roles:users`` 双向一致。

用法：
    python scripts/migrate_users_to_redis.py --dry-run   # 先演练
    python scripts/migrate_users_to_redis.py             # 全量执行
    python scripts/migrate_users_to_redis.py --step disk # 补做单步

依赖：直连 Redis（不经 HTTP），环境变量与 server/main.py 相同
（AGENTS_REDIS_HOST/PORT 或 REDIS_HOST/PORT、REDIS_PASSWORD）。
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_ROOT / "server"))

from roles_store import (  # noqa: E402
    ROLES_FILE,
    USER_ID_MAP_FILE,
    atomic_write_json,
    derive_id_from_name,
)

# 键族与 roles_store 保持同一份定义（roles_store 内为模块私有常量）
K_USERS, K_NAME, K_UA, K_CFG, K_VER = (
    "roles:users", "roles:name", "roles:user_agents",
    "roles:config", "roles:ver",
)
K_PENDING = "roles:pending_disk"
_RESOURCE_PREFIX = "agentscope:user:"
WORKSPACES_DIR = _ROOT / "workspaces"


def build_client():
    """redis.asyncio 客户端（decode_responses 与 roles_store 口径一致）。"""
    import redis.asyncio as aioredis

    host = os.getenv("AGENTS_REDIS_HOST") or os.getenv("REDIS_HOST") or "127.0.0.1"
    port = int(os.getenv("AGENTS_REDIS_PORT") or os.getenv("REDIS_PORT") or "6379")
    return aioredis.Redis(
        host=host, port=port,
        password=os.getenv("REDIS_PASSWORD") or None,
        decode_responses=True,
    )


def load_seed() -> dict:
    """读取旧 roles.json（迁移的输入；缺失即无事可做）。"""
    if not ROLES_FILE.is_file():
        print(f"[skip] 未找到 {ROLES_FILE}——无需迁移（可能已完成或全新环境）")
        raise SystemExit(0)
    try:
        return json.loads(ROLES_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"[abort] 种子文件损坏，拒绝迁移：{exc!r}") from exc


# --- step 1: ids -------------------------------------------------------------
async def step_ids(seed: dict, dry: bool) -> dict:
    id_map: dict = {}
    if USER_ID_MAP_FILE.is_file():
        try:
            id_map.update(json.loads(USER_ID_MAP_FILE.read_text(encoding="utf-8")))
        except Exception:
            print(f"[warn] {USER_ID_MAP_FILE} 损坏，忽略旧映射重新派生")
    for name in (seed.get("users") or {}):
        uid = "admin" if name == "admin" else derive_id_from_name(name)
        id_map[name] = uid  # 派生确定性 ⇒ 重跑恒等
    print(f"[ids] {len(id_map)} 个登录名 → id"
          + ("（dry-run，不落盘）" if dry else ""))
    for name, uid in sorted(id_map.items()):
        print(f"      {name} -> {uid}")
    if not dry:
        atomic_write_json(USER_ID_MAP_FILE, id_map)
    return id_map


# --- step 2: redis -----------------------------------------------------------
async def step_redis(client, seed: dict, id_map: dict, dry: bool) -> None:
    if await client.hlen(K_USERS):
        raise SystemExit(
            "[abort] 顺序守卫：roles:users 已有数据。若此前已迁移过，请勿"
            "重跑 redis 步骤（执行顺序颠倒 = 二次搬家，拒绝执行，9.9-8）。"
        )
    users_h, name_h = {}, {}
    for name, info in (seed.get("users") or {}).items():
        if not isinstance(info, dict):
            continue
        uid = id_map[name]
        record = {"id": uid, "name": name,
                  "role": info.get("role", "member")}
        if info.get("team"):
            record["team"] = info["team"]
        if info.get("password"):
            record["password"] = info["password"]
        users_h[uid] = json.dumps(record, ensure_ascii=False)
        name_h[name] = uid
    ua_h = {}
    for name, allow in (seed.get("user_agents") or {}).items():
        ua_h[id_map.get(name, name)] = json.dumps(allow, ensure_ascii=False)
    cfg_h = {"admin_edit": "true" if seed.get("admin_edit", True) else "false"}
    print(f"[redis] 写入 users={len(users_h)} ua={len(ua_h)} ver=1"
          + ("（dry-run）" if dry else ""))
    if dry:
        for uid, blob in sorted(users_h.items()):
            print(f"      HSET roles:users {uid} = {blob}")
        return
    pipe = client.pipeline(transaction=True)
    pipe.hset(K_USERS, mapping=users_h)
    pipe.hset(K_NAME, mapping=name_h)
    if ua_h:
        pipe.hset(K_UA, mapping=ua_h)
    pipe.hset(K_CFG, mapping=cfg_h)
    pipe.set(K_VER, 1)
    await pipe.execute()


# --- step 3: owner 换键 --------------------------------------------------------
async def step_owner(client, id_map: dict, dry: bool) -> None:
    moved = skipped = 0
    for name, uid in sorted(id_map.items()):
        if name == uid:
            continue  # admin 字面量：键空间不变
        pattern = f"{_RESOURCE_PREFIX}{name}:*"
        suffix_len = len(_RESOURCE_PREFIX) + len(name)
        async for key in client.scan_iter(match=pattern, count=500):
            key = str(key)
            new_key = _RESOURCE_PREFIX + uid + key[suffix_len:]
            if dry:
                print(f"[owner] RENAME {key} -> {new_key}")
                moved += 1
                continue
            try:
                await client.rename(key, new_key)
                moved += 1
            except Exception:
                skipped += 1  # 源键已消失（幂等重跑）
    print(f"[owner] 资源键换键 moved={moved} skipped={skipped}"
          + ("（dry-run）" if dry else ""))


# --- step 4: 磁盘目录改名 ------------------------------------------------------
async def step_disk(client, id_map: dict, dry: bool) -> None:
    from per_user_workspace import safe_user_component

    if not WORKSPACES_DIR.is_dir():
        print("[disk] 无 workspaces 目录，跳过")
        return
    renamed = queued = 0
    for name, uid in sorted(id_map.items()):
        old_c, new_c = safe_user_component(name), safe_user_component(uid)
        if old_c == new_c:
            continue
        for agent_dir in sorted(WORKSPACES_DIR.iterdir()):
            src, dst = agent_dir / "users" / old_c, agent_dir / "users" / new_c
            if not src.is_dir() or dst.exists():
                continue  # 幂等：无事可做 / 目标已存在
            if dry:
                print(f"[disk] rename {src} -> {dst}")
                renamed += 1
                continue
            try:
                src.rename(dst)
                renamed += 1
            except OSError as exc:
                # 跨存储缺口兜底：记入补偿队列，应用侧 5 min 幂等重试
                print(f"[warn] rename 失败（已入补偿队列）: {src}: {exc}")
                await client.rpush(
                    K_PENDING,
                    json.dumps({"op": "rename_dir", "old": name, "new": uid},
                               ensure_ascii=False))
                queued += 1
    print(f"[disk] 目录改名 renamed={renamed} queued={queued}"
          + ("（dry-run）" if dry else ""))


# --- 迁移后校验 ----------------------------------------------------------------
async def verify(client, id_map: dict) -> bool:
    from per_user_workspace import safe_user_component

    problems: list[str] = []
    for name, uid in sorted(id_map.items()):
        # ① name ↔ id 双向一致
        if await client.hget(K_NAME, name) != uid:
            problems.append(f"roles:name[{name}] != {uid}")
        raw = await client.hget(K_USERS, uid)
        if not raw or json.loads(raw).get("name") != name:
            problems.append(f"roles:users[{uid}] 缺失或 name 不符")
        # ② 旧名 owner 键残留 = 0
        if name != uid:
            left = sum(1 async for _ in
                       client.scan_iter(match=f"{_RESOURCE_PREFIX}{name}:*",
                                        count=500))
            if left:
                problems.append(f"旧 owner 键残留 {left} 个：{name}:*")
        # ③ 磁盘旧目录遗留 = 0
        old_c = safe_user_component(name)
        if old_c != safe_user_component(uid) and WORKSPACES_DIR.is_dir():
            for agent_dir in sorted(WORKSPACES_DIR.iterdir()):
                if (agent_dir / "users" / old_c).is_dir():
                    problems.append(f"磁盘旧目录遗留: {agent_dir / 'users' / old_c}")
    if problems:
        print(f"[verify] ❌ {len(problems)} 处不一致：")
        for p in problems:
            print("      -", p)
        return False
    print("[verify] ✅ 双向一致 / 无旧名 owner 键残留 / 无磁盘旧目录遗留")
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将执行的动作，零写入")
    parser.add_argument("--step",
                        choices=["ids", "redis", "owner", "disk", "all"],
                        default="all", help="只执行单步（默认全部）")
    parser.add_argument("--allow-no-aof", action="store_true",
                        help="AOF 未开启时仍允许写入（仅限演练环境）")
    args = parser.parse_args()
    dry = args.dry_run

    seed = load_seed()
    client = build_client()
    await client.ping()

    if not dry and not args.allow_no_aof:
        aof = (await client.info("persistence")).get("aof_enabled", 0)
        if not aof:
            raise SystemExit(
                "[abort] Redis 未开启 AOF 持久化——迁移写入会随重启丢失。"
                "请先按 docker-compose.yml 配置 --appendonly yes 再执行"
                "（演练可加 --allow-no-aof）。")

    id_map = await step_ids(seed, dry)
    if args.step in ("all", "redis"):
        await step_redis(client, seed, id_map, dry)
    if args.step in ("all", "owner"):
        await step_owner(client, id_map, dry)
    if args.step in ("all", "disk"):
        await step_disk(client, id_map, dry)

    if not dry and args.step == "all":
        ok = await verify(client, id_map)
        if not ok:
            raise SystemExit(1)
        print("[done] 迁移完成。旧 roles.json 建议改名留档："
              f"{ROLES_FILE.with_suffix('.json.pre-migration')}")
        if not dry and ROLES_FILE.is_file():
            os.replace(ROLES_FILE,
                       ROLES_FILE.with_suffix(".json.pre-migration"))
    elif dry:
        print("[dry-run] 未写入任何数据（Redis / roles.json / 磁盘零改动）")


if __name__ == "__main__":
    asyncio.run(main())
