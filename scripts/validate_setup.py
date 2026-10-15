# -*- coding: utf-8 -*-
"""端到端验证 agentscope_extra 的部署（API 层面）。

可见性模型（RBAC「归属即规则」）：

- admin（roles.json 中 role=admin 的账号）可见全部智能体，可编辑。
- 智能体全部归属 admin；普通用户按 ``roles.json`` 的
  ``user_agents`` 白名单可见（严格模式：勾选才可见，只读）。
- 两个用户使用同一共享智能体时，会在
  ``workspaces/<agent_id>/users/<user>/`` 下产生两个隔离的工作目录。

认证：脚本以 admin 做 Basic 认证（AGENTSCOPE_PASSWORD），逐用户
检查通过 ``X-On-Behalf-Of`` 代操作完成，无需用户明文密码。

用法：
    python validate_setup.py
"""
import datetime
import os

from common import (
    agents_users,
    api,
    load_env,
    load_registry,
    read_roles,
)

EXPECTED_SHARED = set()
EXPECTED_OWN = {
    "周报助手": {"report_style", "weekly_report"},
    "数据质检员": {"data_check"},
    "个人助手": set(),
}


def main() -> int:
    registry = load_registry()
    if not registry:
        print("registry.json empty — run register_agents.py first.")
        return 1

    try:
        users = sorted(agents_users().keys())
    except RuntimeError as exc:
        print(str(exc))
        return 1
    if not users:
        print("roles.json has no users — nothing to validate.")
        return 1

    env = load_env()
    owner = "admin"
    roles = read_roles()
    user_agents: dict[str, list[str]] = roles.get("user_agents") or {}

    failures: list[str] = []
    hints: list[str] = []
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    # --- 1. 可见性矩阵（admin + X-On-Behalf-Of 逐用户枚举） -----------------
    visible: dict[str, dict[str, tuple[str, bool]]] = {}
    for user in users:
        _, listed = api("GET", "/agent/", user=user)
        visible[user] = {
            view["data"]["name"]: (view["id"], view["editable"])
            for view in listed["agents"]
        }
        print(f"visible[{user}] = {sorted(visible[user])}")

    for name, info in registry.items():
        agent_id = info["agent_id"]
        # 属主全部可见，且可编辑。
        if name not in visible.get(owner, {}):
            failures.append(f"owner '{owner}' cannot see own agent '{name}'")
        else:
            rid, editable = visible[owner][name]
            if rid != agent_id:
                failures.append(f"owner '{owner}': agent id mismatch for '{name}'")
            if not editable:
                failures.append(f"owner '{owner}': own agent '{name}' not editable")

        for user in users:
            if user == owner:
                continue
            granted = agent_id in (user_agents.get(user) or [])
            seen = name in visible.get(user, {})
            if granted:
                if not seen:
                    failures.append(
                        f"{user}: allowlisted agent '{name}' not visible (in user_agents)"
                    )
                else:
                    rid, editable = visible[user][name]
                    if rid != agent_id:
                        failures.append(
                            f"{user}: shared agent '{name}' resolves to wrong id {rid}"
                        )
                    if editable:
                        failures.append(f"{user}: shared agent '{name}' should be read-only")
            elif seen:
                failures.append(f"{user}: non-shared agent '{name}' leaked")

    print()

    # --- 2. 共享智能体的会话隔离 + 技能 -------------------------------------
    share_target = "周报助手"
    if share_target in registry:
        agent_id = registry[share_target]["agent_id"]
        workdirs: dict[str, str] = {}
        for user in [u for u in users if share_target in visible.get(u, {})]:
            _, session = api(
                "POST",
                "/sessions/",
                {"agent_id": agent_id, "name": f"validate-{stamp}"},
                user=user,
            )
            session_id = session["session_id"]
            _, status = api(
                "GET",
                f"/workspace/status?agent_id={agent_id}&session_id={session_id}",
                user=user,
            )
            workdir = status["workdir"]
            workdirs[user] = workdir
            print(f"[{share_target}] {user}: {workdir}")

            marker = os.path.join("users", user)
            if marker not in workdir and f"users/{user}" not in workdir:
                failures.append(
                    f"{share_target}/{user}: workdir is not user-scoped: {workdir}"
                )

            _, skills = api(
                "GET",
                f"/workspace/skill?agent_id={agent_id}&session_id={session_id}",
                user=user,
            )
            names = {s["name"] for s in skills}
            print(f"  skills: {sorted(names)}")
            for expected in EXPECTED_SHARED | EXPECTED_OWN.get(share_target, set()):
                if expected not in names:
                    hints.append(
                        f"{share_target}/{user}: skill '{expected}' missing — "
                        "run sync_skills.py, then re-validate"
                    )
            for skill in skills:
                if not skill.get("markdown"):
                    failures.append(
                        f"{share_target}/{user}: skill '{skill['name']}' has empty markdown"
                    )

        if len(workdirs) > 1 and len(set(workdirs.values())) != len(workdirs):
            failures.append(
                f"{share_target}: workdir collision across users: {workdirs}"
            )
        print()

    # --- 3. Knowledge base infrastructure + embedding catalog -----------------
    import urllib.error
    import urllib.request

    # Endpoints follow the deployment (.env): AGENTS_QDRANT_URL /
    # TEI_BASE_URL override the local defaults (compose deployments use
    # container service names).
    qdrant_url = env.get("AGENTS_QDRANT_URL", "http://localhost:6333").rstrip("/")
    tei_root = env.get("TEI_BASE_URL", "http://localhost:19556/v1").rstrip("/")
    if tei_root.endswith("/v1"):
        tei_root = tei_root[: -len("/v1")]
    for name, url in [
        ("Qdrant", f"{qdrant_url}/healthz"),
        ("TEI", f"{tei_root}/health"),
    ]:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ok = resp.status == 200
        except (urllib.error.URLError, OSError):
            ok = False
        if not ok:
            failures.append(
                f"{name} not reachable at {url} — 拉起 Qdrant / TEI 容器"
                "（本地 start-all.ps1 / 服务器 deploy/docker-compose.server.yml）"
            )
        else:
            print(f"[KB] {name} reachable ({url})")

    try:
        _, catalog = api("GET", "/knowledge_bases/embedding_models", user=owner)
        providers = {
            (p["credential"]["data"] or {}).get("type"): p["models"]
            for p in catalog["providers"]
        }
        print(f"[KB] embedding providers: {sorted(providers)}")
        bge = providers.get("local_bge", [])
        if not any(c["name"] == "bge-small-zh-v1.5" for c in bge):
            hints.append(
                "local_bge embedding catalog misses bge-small-zh-v1.5 — "
                "run configure_embedding.py, check TEI container"
            )
        if "zhipu_coding_plan" in providers:
            failures.append(
                "zhipu_coding_plan leaks embedding cards "
                "(Coding Plan has no embedding API)"
            )
    except RuntimeError as exc:
        hints.append(f"embedding catalog unavailable: {exc}")
    print()

    if hints:
        print("HINTS:")
        for hint in hints:
            print(f"  - {hint}")
    if failures:
        print("FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
