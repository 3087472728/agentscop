# 迁移记录：服务器「下载短链 + 令牌有效期」功能迁入本地

- **日期**：2026-11-12
- **来源**：服务器 `agentrunner@192.168.100.100:/home/agentrunner/agentscop`（git 仓库，
  remote `git@codeup.aliyun.com:.../agentscope.git`）的**未提交本地修改**（`git status`）：
  - `M  main.py`
  - `M  src/agentscop/patches/__init__.py`
  - `M  src/agentscop/role_access_policy.py`
  - `M  config/roles.json`（仅测试用户清理，属运行数据，不迁移）
  - `M  make-dl-link.sh`
  - `?? src/agentscop/patches/download_token_ttl_patch.py`
  - `?? src/agentscop/short_dl_api.py`
- **落点**：本地 `d:\workspace\agentscop\agentscop-platform`（布局不同：
  服务器是官方 `src/agentscop/` 布局，本地是自研 `server/` 包）

## 迁移的功能

### 1. 下载短链服务（新功能）
| 项 | 服务器版 | 本地迁移版 |
|---|---|---|
| 文件 | `src/agentscop/short_dl_api.py` | `server/short_dl_api.py` |
| 签发 | `POST /workspace/files/short-link` | 同左 |
| 跳转 | `GET /dl/{code}`（短码→实时签令牌→302） | 同左 |
| 短链库 | `<项目根>/config/short_links.db` | 同左（经 `server/paths.py`） |
| 对外基地址 | 默认 `http://192.168.100.100:8090/api`（gateway） | 默认 `http://127.0.0.1:8020`（本地直连），`AGENTSCOPE_PUBLIC_API_BASE` 可覆盖 |
| 短码有效期 | 30 天（`AGENTSCOPE_SHORT_LINK_TTL`） | 同左 |

**解决的痛点**：下载链接把签名令牌和完整路径（百分号编码的中文长串）拼在
URL 里，经聊天消息展示/复制后中段丢失，报 `Invalid download token`。
聊天里只放 8 字符短码链接，点击时实时签发新令牌并 302，永不含令牌。

### 2. 下载令牌有效期放宽（60s → 3600s）
| 项 | 服务器版 | 本地迁移版 |
|---|---|---|
| 文件 | `src/agentscop/patches/download_token_ttl_patch.py` | `server/download_token_ttl_patch.py` |
| 机制 | 包装 `WorkspaceService.sign_download_token` 注入默认 TTL | 同左（框架同源，代码一致） |
| 可调 | `AGENTSCOPE_DOWNLOAD_TOKEN_TTL`（秒，默认 3600） | 同左 |
| 注册 | `patches/__init__.py` 聚合 | 本地无 patches 层，`server/app.py` 直接调 `setup()` |

**解决的痛点**：令牌随链接写在聊天里，60 秒内没人点就过期；
且下载签名密钥已持久化（`config/download_secret.key`，重启后历史链接
本就不失效），60 秒 TTL 与该设计意图矛盾。

### 3. 接线（app.py 注册）
`server/app.py` 新增（编号接续既有 16)）：
- `17) setup_download_token_ttl()` —— 安装 TTL 补丁
- `18) setup_short_dl(app, storage)` —— 注册短链路由

### 4. 生成链接脚本（bash → Python 跨平台）
| 项 | 服务器版 | 本地迁移版 |
|---|---|---|
| 文件 | `make-dl-link.sh` | `scripts/make_dl_link.py` |
| 行为 | 剥 `users/<owner>/` 前缀 → 候选用户签短链 → 302 回验 → 输出 `[📥 点击下载](url)`；失败回退 download-token 长链 | 同左，后端地址读 `AGENTS_BACKEND_URL`（默认 `http://127.0.0.1:8020`），属主可从 `data/agentscope.db` 按会话自动解析 |

### 5. `role_access_policy.py` 的 `_find_project_root()` 改动 —— **不迁移**
服务器版是为其布局迁移（`src/agentscop/`）做的项目根向上查找兼容；
本地布局自始固定（`server/paths.py` 的 `ROOT`），无此需求。

### 6. `config/roles.json` —— **不迁移**
服务器上的改动只是清理测试用户（wrp/ly/hly/lh/lh 等），属运行数据。

## 迁移适配点（与服务器版的差异）

1. **身份-属主断层回退**（`_resolve_owner` / `_find_session_owner`）：
   本地 file 模式身份是派生 id（`u_XXX`），而迁移库中会话属主是登录名
   （`agentrunner` 等），框架 `get_session` 按属主匹配会落空（服务器
   Redis 环境身份=登录名=属主，无此问题）。签发短链时先按当前身份
   解析，404 则按 `(agent_id, session_id)` 回退查真实属主，用属主签令牌。
   该断层同样影响本地其他按身份查询的接口（既有问题，非本次引入）。
2. 路径解析改用 `server/paths.py`（`ROOT`/`CONFIG_DIR`）。
3. `PUBLIC_API_BASE` 默认值改为本地直连地址。

## 验证记录（2026-11-12，本地 8020 端口实测）

1. 启动日志出现 `已启用下载令牌有效期补丁（TTL=3600s...）`、
   `下载短链服务已启用...`；`sign_download_token` 签名确认为 `ttl=3600`。
2. `POST /workspace/files/short-link` → `{"code":"fx0DdtF6","url":...,"ttl":2592000}`
3. `GET /dl/fx0DdtF6` → `302`，`Location` 携带实时签发的令牌
   （`...YWdlbnRydW5uZXI...` 即 `agentrunner`，属主回退生效）。
4. 跟随 302 → 令牌校验通过，`skills/.seed/sync_probe/SKILL.md` 内容完整返回。

## 遗留事项

- 本地 `agentscope-main`（editable 框架源码）与服务器框架版本可能存在
  schema/API 演进差异，后续部署新镜像到服务器前需回归验证。
- `tree_sitter` DLL 曾出现一次加载失败（偶发，未复现），若复现可
  `pip install --force-reinstall tree_sitter` 修复。
- 服务器上那批未提交修改仍留在服务器仓库工作区；如需固化，建议在
  服务器上 `git add/commit`（本次未动服务器仓库状态）。
