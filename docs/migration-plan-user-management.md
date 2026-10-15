# 用户管理（团队角色）功能迁移计划

> 源项目：`d:\workspace\agentscop\agent-new`
> 目标项目：`d:\workspace\agentscop\agentscope_extra-master`
> 日期：2026-10-08（v3，新增决定 4：密码 hash 存储）

---

## 0. 已确认的决定

| # | 决定 | 内容 |
|---|------|------|
| 1 | 策略替换 | 直接用 `RoleBasedAccessPolicy` 替换 `OwnedAgentsSharePolicy`，`.env` 共享配置（`AGENTS_OWNER` / `AGENTS_DEFAULT_SHARE` / `AGENTS_AGENT_SHARE`）作废 |
| 2 | 用户列表来源 | 管理页用户列表**统一存 `roles.json`**，不再依赖 `.env` |
| 3 | 用户密码 | 每个用户独立密码存入 `roles.json`，认证改造为从 `roles.json` 读取 |
| 4 | 密码存储安全 | **本期升级为 hash 存储**：`auth.py` 改为哈希校验，`roles.json` 只存哈希值，管理页设置密码时自动哈希化，`.env` 引导导入时旧明文密码自动转换 |

---

## 1. 迁移目标

将源项目的「用户管理」功能（以**团队角色管理页**形式实现）完整迁移到目标项目，并在其上扩展**用户账号管理**（用户名 + 密码 + 角色 + 团队 + 智能体白名单，全部收敛到 `roles.json` 单一数据源）。

迁移后 `roles.json` 目标结构（密码字段存 **hash 值**，格式 `sha256$<salt_hex>$<hash_hex>`）：

```json
{
  "admin_edit": true,
  "users": {
    "admin": { "role": "admin", "password": "sha256$a1b2...$f3e4..." },
    "u2": { "role": "member", "team": "1", "password": "sha256$c5d6...$9a8b..." },
    "u3": { "role": "leader", "team": "2", "password": "sha256$e7f8...$2c3d..." }
  },
  "user_agents": {
    "u2": ["<agent_id_1>"],
    "u3": ["<agent_id_1>", "<agent_id_2>"]
  }
}
```

---

## 2. 迁移内容清单

### 2.1 后端

| # | 源文件 | 目标位置 | 说明 |
|---|--------|----------|------|
| 1 | `server/roles_admin_api.py` | `server/roles_admin_api.py` | `GET/POST /roles` 接口，读写 `config/roles.json`；保存时明文自动哈希化、返回时密码置空（决定 4） |
| 2 | `server/role_access_policy.py` | `server/role_access_policy.py` | `RoleBasedAccessPolicy` RBAC 策略 |
| 3 | （改造）`server/auth.py` | 原地修改 | Basic auth 用户表从 `.env` 改读 `config/roles.json`；校验升级为 PBKDF2 哈希比对（决定 3 + 4） |
| 4 | `config/roles.json` | `config/roles.json`（新建 `config/` 目录） | 初始化：从 `.env` 的 `AGENTS_USERS` 引导生成（密码哈希化落盘） |

### 2.2 前端

| # | 源文件 | 目标位置 | 说明 |
|---|--------|----------|------|
| 5 | `web_ui/frontend/src/pages/team/index.tsx` | 同路径 | 管理页：角色 + 团队 + 智能体白名单 + **新增密码列**（决定 3） |
| 6 | `web_ui/frontend/src/api/role.ts` | 同路径 | `roleApi.get()` / `roleApi.save()` 客户端 |
| 7 | `App.tsx` 的 `/team` 路由 | 同文件追加 | `<TeamPage />` 路由 |
| 8 | `AppSidebar.tsx` 的 Users 入口 | 同文件追加 | 侧边栏图标，`getUserId() === 'admin'` 时显示 |
| 9 | i18n 词条 | `i18n/locales/zh.json` / `en.json` | 新增独立键（如 `userManagement`），与聊天团队面板的 `team` 键区分 |

### 2.3 不迁移的部分

| 源文件 | 不迁移原因 |
|--------|-----------|
| `server/skill_visibility.py` | 目标项目技能机制不同（`skills/.seed` 种子模板），不适用 |
| `server/user_workspace_manager.py` | 目标项目已有等价实现 `per_user_workspace.py` |
| `scripts/migrate_workspaces_to_users.py` | 一次性历史迁移脚本，无复用价值 |

---

## 3. 关键差异与适配点

### 3.1 访问策略互斥 → 直接替换（已确认）

- 目标项目 `server/main.py:137` 注入的 `OwnedAgentsSharePolicy` 将被移除
- `RoleBasedAccessPolicy` 是其功能超集：
  - `user_agents` 白名单 ≈ 原 `AGENTS_AGENT_SHARE` 显式授权
  - 额外支持：KB/凭证全员只读、admin 全量可见/可编辑
- **存量授权转换**：`.env` 的 `AGENTS_AGENT_SHARE=周报助手:u2;数据质检员:u3` 需在迁移时手工转为 `roles.json` 的 `user_agents`（注意：前者按**名称**、后者按 **agent_id**，转换时需查库对应）

### 3.2 存储层不同 → 用户全集改读 `roles.json`（已确认）

- 源项目 `_all_user_ids()` 直接 SQL 查询 SQLite（目标项目是 `RedisStorage`，无 SQLite）
- **方案：** 用户全集 = `roles.json` 的 `users` 键（决定 2），删除 SQLite 查询逻辑
- 数据流：管理页新增用户 → 写入 `roles.json` → 策略 5 秒缓存自动感知

### 3.3 认证改造 → 密码 hash 存入 `roles.json`（决定 3 + 决定 4）

- 现状：`server/auth.py` 的 `load_users()` 从 `.env` 的 `AGENTS_USERS` 解析 `user:pw` 对
- **方案（决定 3）：** 改为读 `config/roles.json` 的 `users.<name>.password` 字段；其余 Basic auth 框架（`basic_auth_user`、`install_basic_auth`）不变
- **方案（决定 4，hash 存储）：**
  - 存储格式：`sha256$<salt_hex>$<hash_hex>`（PBKDF2-SHA256 迭代次数 ≥ 100,000 亦可，实现时定；每用户独立随机盐）
  - 校验逻辑：`basic_auth_user()` 中 `hmac.compare_digest(明文, 文件值)` 改为「解析盐 → 对登录输入做同样哈希 → 常时比对」
  - 写入侧哈希化：`POST /roles` 保存时，若某用户 `password` 字段不是 `sha256$` 前缀格式则视为明文输入，自动哈希化后落盘；前端回显时密码字段永远返回空串（哈希不出网）
  - 引导转换：首次启动从 `.env` 引导生成 `roles.json` 时，旧明文密码**当场哈希化**写入
  - 预估增量工作量：约半天
- 引导逻辑：首次启动若 `roles.json` 不存在 → 从 `.env` 的 `AGENTS_USERS` 生成初始文件（密码哈希化，一次性导入，之后 `.env` 用户表不再参与认证）
- 热生效：`load_users()` 本就每次请求重读，改密码/加用户无需重启
- 兼容说明：为什么不选 bcrypt——目标项目 `requirements.txt` 若无 bcrypt 依赖则需新增；`hashlib.pbkdf2_hmac` 为标准库零依赖，安全性足够，故优先 PBKDF2
- 安全备注（「明文」的澄清）：`.env` 现状**就是明文存储**。`AGENTS_USERS=admin:nMc17asWWOpx` 里的值就是登录密码本身——它是随机生成的强密码，不是加密结果。证据有三：
  1. `auth.py` 模块 docstring 自述：*"plaintext; hash if you expose this beyond a trusted network"*（明文；若暴露到可信网络之外需改为 hash）
  2. `basic_auth_user()` 用 `hmac.compare_digest(登录输入的密码, 文件存储值)` **原文直接比较**——若文件里存的是密文/哈希，任何人都无法登录
  3. 判别方法：**值看起来随机 ≠ 被加密**。加密存储的校验是「对输入做同样加密后比对哈希」，而这里是明文对明文
  - ~~迁移后是否升级为 hash 存储 → 待确认~~ → **已确认本期升级**（决定 4，方案见上方）

### 3.4 目录结构不同

- 源项目：根目录 `main.py` 纯入口 + 各模块 `setup(app)` 注入
- 目标项目：`server/main.py` 直接组装

**方案：** 保留 `setup(app)` 模式，在 `server/main.py` 中 `install_basic_auth(app)` 之后追加调用。

**为什么要追加调用（原理说明）：**

FastAPI 的路由「定义了」不等于「生效了」。`roles_admin_api.py` 的结构是：

```python
def setup(app) -> None:
    @app.get("/roles")          # ← 只有 setup(app) 被调用时，这个装饰器才执行
    async def get_roles(...):   #    路由才真正挂到 FastAPI 应用上
        ...
```

把 `roles_admin_api.py` 复制到目标项目后，它只是磁盘上一个普通的 `.py` 文件。如果没有任何地方调用它的 `setup(app)`：

- 里面的 `@app.get("/roles")` 永远不会执行
- 服务启动后访问 `GET /roles` → 404
- 前端管理页请求接口失败，页面空转

类比：`roles_admin_api.py` 是插件，`setup(app)` 是插头，`server/main.py` 是主机——**插件插上电才工作，复制过来只是放在了桌上**。

两个项目的差异只在于「谁来插电」：

| 项目 | 插电动作的位置 |
|------|----------------|
| 源项目 | 根目录 `main.py:46` → `roles_admin_api.setup(app)`（有专门总入口） |
| 目标项目 | 无总入口，必须由 `server/main.py` 追加这行 |

「追加调用」= 在 `server/main.py` 只加一行 `roles_admin_api.setup(app)`，其他不动。

**为什么放在 `install_basic_auth(app)` 之后：** `install_basic_auth` 把框架身份校验替换为 Basic auth（密码验证），`/roles` 接口的 admin 校验依赖这套认证。先装认证、再挂路由，代码阅读顺序更直观。（技术上先后不影响运行——依赖覆盖在请求时才解析，纯可读性考虑。）

---

## 4. 实施步骤

### 阶段一：后端（改动 4 个文件）

- [x] 1.1 `server/auth.py`：新增哈希工具函数（`hash_password` / `verify_password`，PBKDF2-SHA256 + 独立盐，标准库实现）
- [x] 1.2 `server/auth.py` 改造：
  - `load_users()` 改读 `roles.json`，返回 `{user: hash串}`
  - `basic_auth_user()` 校验改为：解析盐 → 对输入哈希 → 常时比对（替换原 `hmac.compare_digest(明文, 存储值)`）
  - 引导逻辑：`roles.json` 不存在时从 `.env` 的 `AGENTS_USERS` 生成（密码**当场哈希化**）（适配点 3.3）
- [x] 1.3 复制 `roles_admin_api.py` 并改造：
  - admin 校验改用 `Depends(get_current_user_id)`（走 Basic auth）
  - `POST /roles` 保存时：非 `sha256$` 前缀的 `password` 视为明文，自动哈希化落盘
  - `GET /roles` 返回时：密码字段一律替换为空串（哈希不出网）
- [x] 1.4 复制 `role_access_policy.py`：`_all_user_ids()` 重写为读 `roles.json` 的 `users` 键（适配点 3.2）
- [x] 1.5 修改 `server/main.py`：
  - `resource_access_policy=RoleBasedAccessPolicy(...)` 替换 `OwnedAgentsSharePolicy.from_env()`
  - 追加 `roles_admin_api.setup(app)`
- [x] 1.6 存量授权转换：已编写一次性脚本 `scripts/migrate_env_share_to_roles.py`（**需后端启动后运行**：`python scripts/migrate_env_share_to_roles.py`，自动将 `.env` 的 `AGENTS_DEFAULT_SHARE` / `AGENTS_AGENT_SHARE` 按名称查 agent_id 后并入 `roles.json` 的 `user_agents`，幂等可重跑）

### 阶段二：前端（改动 5 个文件）

- [x] 2.1 复制 `api/role.ts`（`client.get/post` 自动带 Basic 头，无需改动；类型扩展 `password` 字段）
- [x] 2.2 复制 `pages/team/index.tsx` 并扩展：
  - 新增用户行：输入用户名 + 初始密码（保存时后端自动哈希化）
  - 用户列表数据源为 `roles.json`（决定 2）
  - 密码列：留空 = 不改，输入新值 = 重置密码（GET 返回空串，前端以「留空保持不变」语义处理）
- [x] 2.3 `App.tsx` 追加 `/team` 路由
- [x] 2.4 `AppSidebar.tsx` 追加 Users 图标入口（admin 判断）
- [x] 2.5 i18n 两个 locale 文件追加词条（独立键 `userManagement`，避免与聊天团队面板的 `team` 冲突）

### 阶段三：验证

- [ ] 3.1 admin 登录 → 侧边栏可见用户管理入口 → 打开 `/team` 页面，用户列表来自 `roles.json`，密码列显示为空
- [ ] 3.2 新增用户（用户名 + 密码）→ 保存 → 检查 `roles.json` 中该用户密码为 `sha256$...` 格式 → **用该账号新登录成功**
- [ ] 3.3 重置某用户密码 → 旧密码登录失败、新密码成功 → `roles.json` 中哈希值已变化
- [ ] 3.4 配置普通用户角色 + 智能体白名单 → 普通用户登录验证：
  - 仅可见白名单内的智能体
  - admin 名下 KB / 凭证只读可见
  - 其他用户私有资源不可见
- [ ] 3.5 `admin_edit` 开关验证：
  - 设 `true`：admin 可编辑/删除他人资源
  - 设 `false`：admin 仅可查看他人资源（编辑接口被拒）
- [ ] 3.6 原 `.env` 存量用户（admin/u2/u3）用原密码登录正常（引导导入哈希化无损）
- [ ] 3.7 哈希不出网验证：`GET /roles` 响应中无任何 `sha256$` 字样
- [ ] 3.8 引导前无 `roles.json` 的全新环境冷启动：自动生成 → 三个存量账号全部可登录

---

## 5. 风险与回滚

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| `.env` 共享配置作废 | 依赖 `AGENTS_AGENT_SHARE` 的存量授权丢失 | 阶段 1.6 手工转换为 `user_agents`；转换前先备份 |
| 认证源切换到 `roles.json` | 引导失败导致所有用户无法登录 | 引导逻辑从 `.env` 无损导入；验证 3.6/3.8 把关；`roles.json` 损坏时回退读 `.env` |
| 哈希校验实现缺陷（盐解析/比对错误） | 全员无法登录 | PBKDF2 用标准库 `hashlib.pbkdf2_hmac` 成熟实现；验证 3.2/3.3/3.6 全链路覆盖；实现保留明文比对回退分支（仅当存储值无 `sha256$` 前缀时兼容旧格式，便于回滚窗口期） |
| 前端两套 `team` 概念混淆 | UI 文案歧义 | i18n 独立键 + 页面标题明确「用户管理」 |
| 删除 `auth.py` 的 `.env` 直读 | 依赖旧机制的其他脚本失效 | 检索 `load_users` 引用方；`.env` 引导保留一版兼容 |

**回滚方案：** 改动集中在 3 个新文件 + `auth.py` / `main.py` 小幅修改，`git checkout` 对应文件即回滚；`roles.json` 为新增文件不影响原逻辑。哈希化留有明文兼容分支，回滚窗口期内新旧格式并存可登录。

---

## 6. 决定记录（原「待确认事项」）

1. ~~策略替换确认~~ → **已确认直接替换**（决定 1）
2. ~~用户列表来源~~ → **已确认统一 `roles.json`**（决定 2）
3. ~~密码管理~~ → **已确认入 `roles.json`，改造 `auth.py`**（决定 3）
4. ~~密码存储方式（明文 vs hash）~~ → **已确认本期升级 hash（PBKDF2-SHA256 + 独立盐）**（决定 4，见 3.3 节）
5. ~~`admin_edit` 含义~~ → **已解释**（见 3.4 节）

---

## 7. 实施记录（2026-10-08 完成编码）

### 7.1 改动文件清单

| 类型 | 文件 | 说明 |
|------|------|------|
| 重写 | `server/auth.py` | PBKDF2 哈希工具（`hash_password` / `verify_password` / `is_hashed`）；`load_users()` 改读 `config/roles.json`；`basic_auth_user()` 哈希校验（保留明文兼容分支）；`bootstrap_roles_file()` 首启引导（.env 明文当场哈希化） |
| 新增 | `server/roles_admin_api.py` | GET/POST `/roles`；admin 校验走 `Depends(get_current_user_id)`；保存时明文自动哈希化、留空继承原哈希；返回时密码置空串 |
| 新增 | `server/role_access_policy.py` | 「归属即规则」策略；用户全集读 `roles.json`；导入路径适配 `agentscope.app.access` |
| 修改 | `server/main.py` | 策略替换为 `RoleBasedAccessPolicy()`；`install_basic_auth` 后追加 `roles_admin_api.setup(app)`；`share_policy.py` 文件保留（不再被引用，便于回滚） |
| 新增 | `web_ui/frontend/src/api/role.ts` | API 客户端（`TeamUser` 扩展 `password` 字段） |
| 新增 | `web_ui/frontend/src/pages/team/index.tsx` | 管理页（密码列：留空不改/输入重置；添加用户需初始密码；智能体白名单多选） |
| 修改 | `web_ui/frontend/src/App.tsx` | `/team` 路由 |
| 修改 | `web_ui/frontend/src/components/layout/AppSidebar.tsx` | Users 图标入口（`getUserId() === 'admin'`） |
| 修改 | `web_ui/frontend/src/i18n/locales/zh.json` / `en.json` | `common.userManagement` 词条 |
| 新增 | `scripts/migrate_env_share_to_roles.py` | 存量授权转换（1.6，幂等，需后端运行后执行） |

### 7.2 静态检查结果

- IDE 诊断（error + warning）：全部 10 个改动文件 **0 问题**
- `py_compile` 语法检查：4 个后端文件（`auth.py` / `roles_admin_api.py` / `role_access_policy.py` / `main.py`）真实执行**通过**（Python 3.14.3）
- 运行时验证（stub 缺失的第三方依赖后加载真实模块，**27/27 通过**）：
  - `auth.py`：PBKDF2 哈希往返、错误密码/空值/畸形存储拒绝、legacy 明文兼容分支、加盐随机性、`.env` 引导导入（roles.json 生成、admin/member 角色落位、明文不落盘）、`load_users` 端到端登录
  - `role_access_policy.py`：判定链全覆盖——普通用户智能体白名单严格模式、admin 名下 KB/凭证全员只读、admin 全量补全（admin_edit=True→EDIT / False→READ）、白名单缺失即不可见
- 前端引用一致性（静态审读）：`roleApi` 与 `client.get/post` 签名匹配；`team/index.tsx` 引用的 9 个 UI 组件（badge/button/empty/input/skeleton/select/separator/table/utils）全部存在，`Button` 支持 `tooltip`/`xs`/`icon-sm`；`AgentView.id`/`data.name` 类型匹配；自定义 tailwind 类（`surface-muted`/`text-data` 等）已在 `index.css` 定义；`common.userManagement` zh/en 词条均在
- 本机 Node 运行时不可用（安装器进程锁），`tsc`/`pnpm build` 未能在本地执行——首次启动后跑一次 `pnpm build` 或直接 `pnpm dev` 确认编译

### 7.3 部署顺序

1. 启动后端（首次启动自动从 `.env` 引导生成 `config/roles.json`，密码哈希化）
2. 验证存量账号登录（验证项 3.6）
3. 运行 `python scripts/migrate_env_share_to_roles.py` 转换存量授权（1.6）
4. 按「阶段三：验证」清单逐项验收（3.1–3.8）
5. 验收通过后可从 `.env` 删除 `AGENTS_OWNER` / `AGENTS_DEFAULT_SHARE` / `AGENTS_AGENT_SHARE`（保留 `AGENTS_USERS` 无害，仅作引导兜底）

**全新部署（无 `.env`）路径**：引导不触发 → 认证关闭（header 行为）→ 用 `admin` 用户名进入管理页 → **首次保存必须为每个用户（含 admin 自己）输入初始密码**（后端拒绝无密码账号，422 提示），保存后 `roles.json` 生成、Basic auth 即刻启用。
