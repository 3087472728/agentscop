# -*- coding: utf-8 -*-
"""归档（改名打标）指定用户的磁盘工作区目录。

用途：管理页删除用户后，其 ``workspaces/<agent>/users/<user>/``
（记忆、会话产物、技能）不会被程序自动删除；本脚本把目录改名为
``<user>.deleted-<时间戳>`` —— 只改名不删除，随时可人工恢复。

用法：
    python archive_user_workspace.py <user>              # 预览（dry-run）
    python archive_user_workspace.py <user> --apply      # 实际改名
    python archive_user_workspace.py <user> --agent 数据质检员 --apply
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import WORKSPACES_DIR  # noqa: E402
from per_user_workspace import safe_user_component  # type: ignore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user", help="要归档的用户名")
    parser.add_argument("--agent", default=None, help="仅处理该智能体目录")
    parser.add_argument(
        "--apply", action="store_true", help="实际执行改名（默认 dry-run 预览）"
    )
    args = parser.parse_args()

    if not WORKSPACES_DIR.is_dir():
        print(f"workspaces dir not found: {WORKSPACES_DIR}")
        return 1

    comp = safe_user_component(args.user)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    targets: list[Path] = []
    roots = (
        [WORKSPACES_DIR / args.agent]
        if args.agent
        else sorted(p for p in WORKSPACES_DIR.iterdir() if p.is_dir())
    )
    for agent_dir in roots:
        user_dir = agent_dir / "users" / comp
        if user_dir.is_dir():
            targets.append(user_dir)

    if not targets:
        print(f"no workspace dirs found for user '{args.user}' (component={comp})")
        return 0

    mode = "RENAME" if args.apply else "DRY-RUN"
    for user_dir in targets:
        target = user_dir.parent / f"{comp}.deleted-{stamp}"
        print(f"[{mode}] {user_dir}  ->  {target}")
        if args.apply:
            if target.exists():
                print(f"  SKIP: target already exists")
                continue
            user_dir.rename(target)
    if not args.apply:
        print("\n(dry-run) add --apply to perform the renames.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
