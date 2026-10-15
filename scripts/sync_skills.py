# -*- coding: utf-8 -*-
"""把技能母本同步进每个用户的工作区分区。

目标：每个已注册智能体的工作区 × 每个已知用户（.env 中
``AGENTS_USERS`` 的键）加上磁盘上已存在的任意用户目录
（``workspaces/<agent_id>/users/*`` —— 覆盖共享智能体的访客）。
两套机制配合（框架语义，v2.0.8）：

- 播种：工作区管理器把 ``skill_paths``（共享母本，在
  server/main.py 中接线）播种到新工作区的 ``skills/.seed`` ——
  但仅在种子为空时。因此本脚本也要覆写种子模板，让之后装配的
  分区能拿到母本更新。
- 已装配分区：``skills/<agent_id>/`` 在首次使用时从种子复制，
  之后原地编辑；本脚本增量覆写它（共享 + 智能体自有母本），
  更新在下一次 ``list_skills`` 即生效，无需重新装配。

平台通过"分区 mtime 对比 ``.index``"来调和带外分区变更
（增/删），因此直接复制就够了。

用法：
    python sync_skills.py                 # 同步所有已注册智能体
    python sync_skills.py --agent qa_agent
    python sync_skills.py --prune         # 同时删除非母本的技能目录
"""
import argparse
import json
import shutil

from common import (
    AGENTS_DIR,
    SKILLS_DIR,
    WORKSPACES_DIR,
    agents_users,
    discover_skills,
    load_registry,
    safe_component,
    skill_name_of,
)


def agent_dir_name_map() -> dict[str, str]:
    """映射 显示名（agent.json 的 `name`）-> 智能体目录名。"""
    mapping = {}
    if AGENTS_DIR.is_dir():
        for agent_dir in sorted(p for p in AGENTS_DIR.iterdir() if p.is_dir()):
            cfg_file = agent_dir / "agent.json"
            if cfg_file.is_file():
                mapping[
                    json.loads(cfg_file.read_text(encoding="utf-8"))["name"]
                ] = agent_dir.name
    return mapping


def shared_masters() -> list:
    return discover_skills(SKILLS_DIR)


def own_masters(agent_dir_name: str) -> list:
    return discover_skills(AGENTS_DIR / agent_dir_name / "skills")


def check_unique(masters: list, agent_dir_name: str) -> None:
    seen: dict[str, str] = {}
    dupes = []
    for master in masters:
        name = skill_name_of(master)
        if name in seen:
            dupes.append((name, seen[name], master))
        seen[name] = master
    if dupes:
        lines = "\n".join(
            f"  '{n}': {a} vs {b}" for n, a, b in dupes
        )
        raise SystemExit(f"Duplicate skill names for agent '{agent_dir_name}':\n{lines}")


def known_users(agent_id: str) -> list[str]:
    """AGENTS_USERS 的键 + 磁盘上已存在的用户目录。"""
    users = set(agents_users().keys())
    users_dir = WORKSPACES_DIR / agent_id / "users"
    if users_dir.is_dir():
        users |= {d.name for d in users_dir.iterdir() if d.is_dir()}
    return sorted(users)


def drop(masters: list, target_dir, prune_set: set | None) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for master in masters:
        shutil.copytree(master, target_dir / master.name, dirs_exist_ok=True)
    if prune_set is not None:
        for child in sorted(target_dir.iterdir()):
            if child.is_dir() and child.name not in prune_set:
                shutil.rmtree(child)
                print(f"    pruned {child.name}")


def sync_user(agent_id: str, user: str, shared: list, own: list, prune: bool) -> None:
    user_dir = WORKSPACES_DIR / agent_id / "users" / safe_component(user)
    skills_root = user_dir / "skills"

    # 1. 用共享母本刷新种子模板 —— 之后装配的分区从更新后的母本
    #    出发。带 --prune 且共享母本已清空时，过期的种子整体删除。
    if shared:
        drop(shared, skills_root / ".seed",
             {m.name for m in shared} if prune else None)
    elif prune and skills_root.joinpath(".seed").is_dir():
        shutil.rmtree(skills_root / ".seed")
        print("    pruned stale .seed (no shared masters)")

    # 2. 增量覆写已装配分区（共享 + 自有）。
    partition = skills_root / agent_id
    drop(shared + own, partition,
         {m.name for m in shared + own} if prune else None)
    print(
        f"  synced user '{safe_component(user)}' "
        f"(seed {len(shared)}, partition {len(shared) + len(own)})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        default=None,
        help="Filter by agent directory name or display name",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove skill dirs absent from the master sets",
    )
    args = parser.parse_args()

    registry = load_registry()
    if not registry:
        print("registry.json empty — run register_agents.py first.")
        return 1

    shared = shared_masters()
    name_map = agent_dir_name_map()
    target = args.agent
    selected = []
    for display, info in sorted(registry.items()):
        dir_name = name_map.get(display, "")
        if target and target not in (display, dir_name):
            continue
        own = own_masters(dir_name or display)
        check_unique(shared + own, dir_name or display)
        selected.append((display, info["agent_id"], own))

    if target and not selected:
        print(f"Agent '{target}' not found in registry.")
        return 1

    print(f"Workspaces basedir: {WORKSPACES_DIR}")
    print(f"shared masters: {[m.name for m in shared]}")
    for display, agent_id, own in selected:
        print(f"[{display}] own masters: {[m.name for m in own]}")
        for user in known_users(agent_id):
            sync_user(agent_id, user, shared, own, args.prune)

    print("\nDone. Updates land on each user's next list_skills call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
