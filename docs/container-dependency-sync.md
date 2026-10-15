# 容器依赖同步冲突处理记录（container-dependency-sync）

> 记录日期：2026-10-09
> 涉及环境：`agentscope_extra-master` Docker 部署（`docker-compose.yml`）
> 冲突性质：**源码已更新、容器内依赖滞后**——共 2 起，同一模式，本文统一整理成因、处置与预防规范。
> （同日另发生一起 **git 合并冲突**，性质与处置完全不同，见第八节。）

---

## 一、环境架构（冲突的土壤）

本项目 Docker 部署采用「**源码 bind mount + 依赖固化**」模式：

| 层 | 内容 | 更新时机 |
|----|------|----------|
| 源码层 | `./web_ui/frontend`、`./server` 等目录 bind mount 进容器 | 宿主机保存文件**即时生效**（Vite HMR / uvicorn `--reload`） |
| 依赖层（web） | named volume `web-frontend-node-modules` 内的 node_modules | 仅在**容器启动**时由启动脚本执行 `pnpm install` |
| 依赖层（server） | 镜像内 Python site-packages | 仅在**镜像构建**时由 Dockerfile 执行 `pip install -r requirements.txt` |

即：**代码是活的，依赖是死的**。当某次改动新增了第三方依赖而未同步依赖层时，热加载的新代码就会 import 失败——这就是本次两起冲突的共同根源。

```
宿主机 package.json / requirements.txt（新增依赖）
        │ bind mount（即时）
        ▼
容器内新代码 ──import──✗──> 容器内旧依赖 → 崩溃
```

---

## 二、冲突 1：前端缺 katex（web 容器）

### 2.1 现象

浏览器 Vite 报错浮层：

```
[plugin:vite:import-analysis] Failed to resolve import "katex/dist/katex.min.css"
from "src/components/markdown/index.tsx". Does the file exist?
```

报错位置：`web_ui/frontend/src/components/markdown/index.tsx` 第 8 行
`import "katex/dist/katex.min.css";`，页面无法渲染。

### 2.2 排查过程

1. `frontend/package.json` 中已声明 `"katex": "^0.16.27"`，源码侧没问题；
2. 容器内 `node_modules` 位于 named volume `web-frontend-node-modules`，
   其中依赖是按**旧版 package.json** 安装的，没有 katex；
3. 源码经 bind mount 已热更新到位，依赖层未动 → 解析失败。

**附带勘误**：`docs/design.md` 中「`@streamdown/math` 已含 katex，无需单独安装」的说法
**在 pnpm 严格 node_modules 布局下不成立**——传递依赖无法被应用代码直接 import。
只要代码里 `import 'katex/...'`，就必须在 `package.json` **显式声明**（该声明是必要的，只是当时没装上）。

### 2.3 处置

利用启动脚本「容器启动时自动 `pnpm install`」的特性，重启即可补装：

```powershell
docker compose -f d:\workspace\agentscop\agentscope_extra-master\docker-compose.yml restart web
```

- pnpm store 有 volume 缓存，补装 96 个包约 18 秒；
- Vite 检测到 lockfile 变化后自动重新预优化依赖并正常启动。

### 2.4 验证

```powershell
docker logs --tail 40 agentscope-extra-web-1          # 确认 pnpm install 与 Vite 启动正常
docker exec agentscope-extra-web-1 ls -la /app/frontend/node_modules/katex/dist/katex.min.css
```

`katex.min.css` 存在，刷新浏览器报错消失。

### 2.5 持久性

`pnpm install` 按 lockfile 补装进 named volume，**容器重启后仍在**（volume 持久化）。
长期注意：volume 内增量补装可能积累孤儿包，可择机 `docker compose down -v` 后全新安装清理（会清数据）。

---

## 三、冲突 2：后端缺 pymupdf（server 容器）

### 3.1 现象

前端「用户管理 / 凭证 / 频道」等页面全部空白——不是前端问题，
而是**后端所有 API 不可用**：`main.py` 新代码 `import` 了 `pymupdf`（`fitz`），
镜像里没装 → `ModuleNotFoundError` → uvicorn `--reload` 热加载后进入**崩溃循环**，
全部接口 502/断连，前端各页拿不到数据。

### 3.2 排查过程

1. `requirements.txt` 中**已**声明 `pymupdf==1.28.2`（源码侧没问题）；
2. 但 server 镜像是按旧版 requirements 构建的，pip 依赖层没有 pymupdf；
3. 代码热加载、依赖未更新 → import 崩溃循环。

### 3.3 处置（临时修复）

```powershell
# 容器可写层内补装（走清华源加速）
docker exec agentscope-extra-server-1 pip install pymupdf==1.28.2 -i https://pypi.tuna.tsinghua.edu.cn/simple
docker restart agentscope-extra-server-1
```

### 3.4 验证

- `docker logs --tail 50 agentscope-extra-server-1`：无 import 崩溃，uvicorn 正常启动；
- 请求 `/credential/` 返回 200；`/roles` 由异常恢复为正常的 401 鉴权响应；
- 前端用户管理 / 凭证 / 频道页面恢复显示。

### 3.5 持久性（**重要残留风险**）

容器可写层里的 `pip install` **随容器重建/换机丢失**。
`requirements.txt` 已包含声明，根治只需把依赖烧进镜像（见第四节待办）。

---

## 四、待办合并动作

| 优先级 | 动作 | 目的 |
|--------|------|------|
| **必做** | `docker compose build server && docker compose up -d server` | 把 pymupdf 烧进镜像，消除容器重建后复发风险 |
| 建议 | 择机 `docker compose down -v` 后全新安装 | 清理 web 依赖 volume 的孤儿包（**会清数据**，需选窗口期） |

```powershell
cd d:\workspace\agentscop\agentscope_extra-master
docker compose build server
docker compose up -d server
```

---

## 五、今后的依赖变更规范（预防）

**凡改动依赖声明文件，必须同步依赖层**：

| 改动的文件 | 依赖所在层 | 生效方式 |
|------------|-----------|----------|
| `web_ui/frontend/package.json` | named volume | `docker compose restart web`（启动脚本自动 `pnpm install`） |
| `requirements.txt` | server 镜像 | `docker compose build server && docker compose up -d server` |

可选统一习惯：依赖变更后一次性 `docker compose build web server && docker compose up -d web server`，宁可慢勿遗漏。

---

## 六、快速诊断口诀

> **页面突然全空/白屏 → 先看后端日志，再怀疑业务。**

```powershell
docker logs --tail 50 agentscope-extra-server-1
```

- 出现 `ModuleNotFoundError` 等崩溃循环 → 九成是「新代码 × 旧依赖」冲突，按第五节对应流程同步依赖；
- 后端正常（能返回 401/200）→ 才往前端渲染、路由、鉴权方向查。

**本次顺带确认的既有设计（非冲突，勿误修）**：
- 用户管理页仅 admin 可见；
- 频道路由仅返回本人创建的频道（当前策略未覆盖 channel）；
- 普通用户只读可见 admin 创建的公开凭证。

---

## 七、附录：命令速查

```powershell
# ---- 状态排查 ----
docker compose -f d:\workspace\agentscop\agentscope_extra-master\docker-compose.yml ps
docker logs --tail 50 agentscope-extra-server-1        # 后端崩溃循环常在这
docker logs --tail 40 agentscope-extra-web-1           # 前端依赖/Vite 问题在这

# ---- web 依赖同步（package.json 变更后）----
docker compose -f d:\workspace\agentscop\agentscope_extra-master\docker-compose.yml restart web
docker exec agentscope-extra-web-1 ls -la /app/frontend/node_modules/<pkg>

# ---- server 依赖同步（requirements.txt 变更后，根治方式）----
docker compose -f d:\workspace\agentscop\agentscope_extra-master\docker-compose.yml build server
docker compose -f d:\workspace\agentscop\agentscope_extra-master\docker-compose.yml up -d server

# ---- server 临时补包（仅当前容器实例，重建即失效）----
docker exec agentscope-extra-server-1 pip install <pkg>==<ver> -i https://pypi.tuna.tsinghua.edu.cn/simple
docker restart agentscope-extra-server-1
```

---

## 八、同日第二类冲突：git 合并冲突（master ↔ origin/master）

> 与上文容器依赖冲突同日发生（2026-10-09），但性质不同：依赖冲突是「新代码 × 旧依赖」的**运行时**问题，
> 解法是补齐依赖层；合并冲突是本地与远程分叉后的**内容取舍**问题，必须逐文件人工判断，没有一键解法。

### 8.1 背景

| 项 | 内容 |
|----|------|
| 前置修复 | `master` 无上游跟踪，pull 报 "no tracking information" → `git branch --set-upstream-to=origin/master master` |
| 分叉形态 | 本地 ahead 2（RBAC 改造：`f8e9845` 迁移 + `ef8983d` 核心接线），behind 18（远程知识库 KB + 部署功能一整批） |
| 合并方式 | `git pull --no-rebase origin master`（merge，保留分叉历史），自动合并大部分，剩 **6 个文件冲突** |

**核心取舍原则**：本地的「RBAC 体系」是方向性取代，远程的「KB/部署能力」是增量补充——
功能接线以本地 RBAC 为准；独立能力（KB、解析器、部署参数化）全盘保留远程；两者交集处融合。

### 8.2 六个冲突文件处置总览

| 文件 | 冲突类型 | 处置 | 一句话理由 |
|------|----------|------|-----------|
| `server/main.py` | 内容 ×4 | 逐项融合（见 8.3） | 本地 RBAC 接线 + 远程 KB/部署接线，两者都要 |
| `docs/design.md` | 内容 ×2 | 三方重合并（见 8.3） | IDE 自动解决曾丢失远程 ~232 行，重做 |
| `AGENTS.md` | 内容 ×2 | 取并集 | 双方各自补充文档条目，无对立 |
| `docs/guide.md` | 内容 | 融合 | 操作速查表：RBAC 新方式 + 远程 KB 步骤 |
| `registry.json` | 内容 | **取本地** | 生成物，映射本地实例（含「海河天津站」等 8 个 agent，与本地 `workspaces/` 吻合）；远程 id 属部署服务器实例，取回会错配 |
| `scripts/_auth_probe.py` | 改删 | **保留远程** | 本地曾删（探针移去 tmp/），远程已升级为参数化运维工具（`BASE_URL` + HTTPS SSL 上下文，服务 8005 部署），且其依赖 `http_ssl_context`/`auth_headers` 已验证存在于合并后的 `common.py` |

### 8.3 关键处置明细

**`server/main.py`（4 处冲突，逐项取舍）**：

| 冲突点 | 本地 | 远程 | 取舍 |
|--------|------|------|------|
| Redis 连接 | `REDIS_HOST`（compose 用） | `AGENTS_REDIS_HOST/PORT`（服务器部署用） | **两套兼容**：`os.getenv("REDIS_HOST", ...)` 兜底 |
| `extra_credentials` | 仅 Zhipu | Zhipu + **LocalBgeCredential** | 取远程（KB 建库必需 BGE embedding） |
| `resource_access_policy` | **RoleBasedAccessPolicy()** | OwnedAgentsSharePolicy.from_env() | 取本地（RBAC 取代已废弃的 `.env` 共享） |
| `knowledge_parsers` | 无 | Text / PdfWithFigures / Word / Excel / PPT | 取远程（KB 上传解析链） |
| 路由注册 | `roles_admin_api.setup(app)` | SPA 静态托管 catch-all | **双保留**：显式 API 路由先注册，catch-all 兜底在后（FastAPI 按注册顺序匹配，顺序不能反） |
| uvicorn 启动 | port=8000 写死 | `BACKEND_PORT` + `AGENTS_RELOAD` + SSL | 取远程（超集，默认值即本地旧行为） |

**`docs/design.md`**：IDE 自动解决取了「当前侧」，远程约 232 行凭空消失。改用
`git merge-file --diff3`（base=`78f2eb8`，ours=master，theirs=origin/master）三方重跑，
只剩 2 处真冲突：§10 演进路线本地「细粒度授权」更新 + 远程 4 条 KB 路线条目拼接去重；
文件末尾本地 Phase 5（RBAC → §13）与远程 §14（知识库落地记录）**双保留**，先后排列。

### 8.4 教训：IDE/工具的「自动解决」不可信

1. **`main.py` 被解成不可运行状态**：保留了远程的 `OwnedAgentsSharePolicy.from_env()` 调用
   却丢了对应 import（启动必 NameError）；`LocalBgeCredential`/`PdfWithFiguresParser` 导入了却没用上，
   `knowledge_parsers` 块整块丢失——必须按上表逐项人工复核；
2. **`design.md` 丢大段内容**：自动解决按「取当前侧」处理双追加，远程整节蒸发——
   大改动的文档用 `git merge-file` 三方重跑比手工取舍可靠。

### 8.5 验证清单与结果

- `py_compile server/main.py` 通过；
- 全部文件无 `<<<<<<<` 残留标记；
- `git diff --name-only --diff-filter=U` 为空后才逐个 `git add` + commit；
- 合并提交 `b96eb78`，推送 `9fb5275..b96eb78`，`master...origin/master` 归零同步。

---

> 相关文档：`docs/design.md`（其中「@streamdown/math 已含 katex」表述待按 2.2 勘误修正）、
> `docs/migration-plan-user-management.md`。
