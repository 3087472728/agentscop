# 用户管理（RBAC）模块评审意见

> 评审范围：Phase 5 用户管理与 RBAC（`server/auth.py`、`server/roles_admin_api.py`、`server/role_access_policy.py`、`web_ui/frontend/src/pages/team/` 及脚本层适配）。
> 总评：主体思路符合工程约定——走原生扩展点零本体改动、roles.json 单一配置 + 5 秒热生效、密码哈希不出网、设计文档同步，这些都没问题。以下四个问题集中在同一点上：**「roles.json 是唯一事实源」只贯穿了认证和可见性两层，脚本层和运行数据层没有跟上。**

---

## 1. 脚本层用户表仍读 `.env`，与 roles.json 形成双事实源（高）

迁移计划决定 2 约定「用户列表统一存 roles.json，不再依赖 .env」，认证和可见性两层做到了，脚本层没有：

- `scripts/common.py:58-60`：`agents_users()` 仍只读 `.env` 的 `AGENTS_USERS`；
- `scripts/common.py:94-110`：`auth_headers()` 用 `.env` 里的明文密码构造 Basic 头；
- 调用方：`configure_model.py:121`、`configure_embedding.py:59`、`validate_setup.py:40`、`chat_smoke.py`、`_auth_probe.py`。

两个直接后果：

1. UI 上重置任意用户密码后，`.env` 里的明文即过期——`chat_smoke` / `configure_model` 等脚本对该用户全部 401，且报错指向 AGENTS_USERS，排障方向是错的；
2. UI 新增的用户不在脚本用户清单里，`configure_embedding` 永远跑不到他——新用户拿不到 `local_bge` 凭据，建不了知识库（对照 `server/model_policy.py` 的 embedding 目录逻辑）。

改造方向：用户清单收敛到 roles.json；认证口令需要另行划界（roles.json 里只有哈希，脚本拿不到明文——比如运维通道只保留 admin + 环境变量注入，或提供双写工具，各有利弊需要权衡）。`sync_skills.py:77-83` 用「磁盘已存在目录」兜底用户清单的做法可以参考，但它不需要认证头，不能直接照搬。定稿后在 design.md 把「用户表的事实源」说成一句话。

## 2. 删除用户没有生命周期处理（高）

`POST /roles` 是全量覆写 users（`roles_admin_api.py:108-129`），前端删行后保存即生效（`team/index.tsx:280-306`），整个删除动作没有校验、没有钩子。逐层看留下的痕迹：

| 层 | 现状 | 问题 |
|---|---|---|
| 磁盘 | `workspaces/<agent>/users/<user>/`（记忆、会话产物、技能分区）原样残留 | 无清理/归档约定；里面是该用户全部数据，不宜简单 rmtree |
| Redis | 该用户名下自建的 agent/KB/凭证记录仍在 | `role_access_policy.py:121-123` 的 admin 分支只枚举 roles.json **在册** owner——已删用户的资源对所有人生效不可见，也无法从 UI 清理，只能直连 Redis。等于「归属即规则」模型里出现了一个未定义状态（幻影资源） |
| user_agents | 前端保存时从表格行重建，该用户条目被顺带清掉 | 这一处反而是闭环的 |

改造方向：保存时 diff 出被移除的用户（对比 existing 与 payload 即可）；该用户名下还有资源时，考虑拦截/二次确认，或允许删除但给 admin 一条兜底可见/清理路径——注意兜底做在哪一层（策略 or API），尽量不破坏「归属即规则」的简洁性；磁盘工作区建议提示路径由管理员手动归档。design.md 目前没有「删除用户」的行为定义，需要补。

## 3. admin 判定两套标准（中）

| 位置 | 判定 |
|---|---|
| `role_access_policy.py:120` | `role == "admin"`（读 roles.json，可配多个 admin） |
| `roles_admin_api.py:44-46` | `user_id != "admin"`（硬编码用户名） |
| `team/index.tsx:225`、`AppSidebar.tsx` | `getUserId() === 'admin'`（硬编码用户名） |

现在只有一个名为 admin 的账号所以无感；一旦在 roles.json 给第二个人配 admin 角色，会出现「策略放行看所有资源、但进不了管理页也改不了 /roles」的分叉。应统一以 roles.json 的 role 字段为准。

## 4. 用户名未校验，可能撞穿文件隔离（中）

`/team` 页允许任意字符用户名，但身份落盘前会净化：`server/per_user_workspace.py:36-43` 把非 `[A-Za-z0-9_-]` 替换为 `_` 并截断 64。共享模式下 `users/<user>` 是用户间**唯一**的文件隔离边界（design.md §13 Phase 4）——中文用户名「张三」「李四」会净化成同一个目录 `__`，隔离直接失效。

另外 `scripts/common.py:148-153` 手工镜像了一份同款净化逻辑，两处必须保持同步，目前没有任何机制保证这一点。

改造方向：`/roles` 保存入口增加用户名白名单校验（规则需与 `safe_user_component` 对齐，保证净化后无碰撞），前端同步提示；「用户名即路径组件」这条约定记入 design.md。

---

## 其他（bug 级，另行安排）

- `roles_admin_api.py:49-57` `_read_roles()` 任何异常都静默返回空骨架，文件损坏时表现为「看起来没有用户」；
- `POST /roles` 全量覆写，无并发保护，两人同时保存后者覆盖前者；
- 前端删除用户仅删本地行，不点「保存更改」不生效且无任何提示。

---

问题 1、2 属于架构闭环缺口，建议动手前先出一页简短方案对齐思路（尤其是 1 里「脚本认证口令从哪来」和 2 里「兜底可见放哪层」这两个取舍点）；改动涉及设计语义的，按工程约束同步 design.md。
