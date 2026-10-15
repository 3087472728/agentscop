# 计划执行记录（部署链路）

> 部署相关工作的执行流水：每完成一步 / 解决一个问题就追加记录，**最新在上**。
> 链路设计与命令细节见 [DEPLOY-ALIYUN.md](DEPLOY-ALIYUN.md)，本文只记「做了什么、结果如何」。

## 状态总览（每次执行后更新）

| # | 阶段 | 状态 | 说明 |
|---|---|---|---|
| 0 | 环境清理 | ✅ 2026-10-14 | 删除旧项目副本、误建空 git 仓库、会话临时产物 |
| 1 | ACR 仓库准备 | ✅ 2026-10-14 | 命名空间 `agentrunner`，自动建仓开启，固定密码已设 |
| 2 | 应用镜像构建推送 | ✅ 2026-10-14 | `agentrunner/agentscop-platform:20261014-133852` / `20261014-134119` |
| 3 | 上游镜像中转 | ✅ 2026-10-14 | `redis:7` / `qdrant:v1.19.1` / `tei:cpu-1.8` 已推上 ACR |
| 4 | 服务器部署 | ✅ 2026-10-14 | 新栈 6020 四容器 healthy（应用镜像 + 薄补丁层 `custom-20261015`） |
| 5 | 验证冒烟 | ✅ 2026-10-14 | validate / kb_smoke / chat_smoke 全绿；浏览器人工验收 ⬜ |
| 6 | 数据迁移（8020 → 新栈） | ⬜ 另行立项 | 迁移验收前旧预研服务（127.0.0.1:8020）保持运行 |

**约定不变量**：服务器上公司栈（8005）、其他项目容器一律不动；新栈唯一对外端口 6020。

---

## 2026-10-15：服务器补丁回迁本地，框架层改动两处落地

> 源：服务器上 `server-patches` 分支（基于官方 v2.0.10）的 2 个 commit，
> 以 `d:\workspace\agentscop\agentscope-server-patches-20261015.tar.gz`（含 2 个 git patch
> + README-WINDOWS.md）带回，解压留存于 `d:\workspace\agentscop\patches-20261015\`。
> 本地框架源码 `agentscope-main` 为 2.0.10dev（比 PyPI 2.0.10 多 message-index 等代码），
> 无法直接 `git apply`，逐处手工套用。

### 框架层改动（`d:\workspace\agentscop\agentscope-main`）

| 文件 | 改动 | 作用 |
|---|---|---|
| `src/agentscope/app/storage/_redis_storage.py` | 第 785 行（`list_agents` 返回前）加 `records.sort(key=lambda r: r.created_at)` | 智能体列表按创建时间升序，最早创建在前（= 前端默认智能体） |
| `src/agentscope/embedding/_openai/_models/bge-small-zh-v1.5.yaml` | 新增模型卡（512 维，TEI 本地服务） | 创建知识库选 embedding 时可选 `bge-small-zh-v1.5` |

### 镜像侧同步（`agentscop-platform`）

镜像内框架来自 PyPI `agentscope==2.0.10`，本地源码改动不会自动进镜像，故：

- `deploy-patches/container/{_redis_storage.py, bge-small-zh-v1.5.yaml}`：服务器补丁后完整文件，与线上 `custom-20261015` 薄镜像内字节一致
- `deploy/Dockerfile`：pip install 后 `COPY` 上述两文件覆盖 site-packages 对应路径 → **新构建镜像自带补丁**，服务器薄覆盖镜像（`deploy-patches/Dockerfile`）降级为热修兜底
- `deploy/DEPLOY-ALIYUN.md`：同步为服务器新版（部署完成状态 + `AGENTSCOPE_API` 排障项）

### ⚠️ 今后规则

**改框架代码（agentscope-main）必须同步两处**：本地源码 + `deploy-patches/container/`（或更新 Dockerfile 覆盖逻辑），否则镜像不带改动。业务代码（`server/`、`web_ui/`、`scripts/` 等）无此问题，改完构建即可。

### 顺带修复：venv editable 死链（重启前必看）

`__editable__.agentscope-2.0.10.pth` 仍指向已不存在的 `agentscope-official\src`
（目录重组后改名 `agentscope-main` 未同步指路条），venv 实际**导入不了 agentscope**
——在跑后端仅靠内存旧模块存活，直接重启即挂。已改指 `D:\workspace\agentscop\agentscope-main\src`，
验证导入正常（2.0.10dev）且排序行生效。

### 目录归属澄清（2026-10-15 用户确认，同日补充）

- `agentscop-platform`（本仓库）= **个人研究项目**，部署在个人阿里云服务器（新栈 6020）
- `agentscope_extra-master` = **公司开发项目**，同为**活跃开发目录**（不是待删旧副本）
- 两仓库**有意保持差异**：本仓库相对公司项目的改动未同步回去，**有意保留**；
  尚未迁移的部分由使用者**手动恢复**，不做任何自动同步/清理
- 两个项目共用 `..\.venv`（editable 指向 `agentscope-main` 2.0.10dev）：
  在哪个目录启动，即加载哪套业务代码 + 同一份框架源码

### 状态

- 本地：重启后端（`.\stop-all.ps1` → `.\start-dev.ps1`）后排序 / 模型卡生效；redis/qdrant/tei 容器无需动
- 服务器：下次 `build-push.ps1` 出新 tag 后，`.env` 的 `AGENTS_IMAGE` 换新 tag → `pull && up -d`，与 `custom-20261015` 等效且免去薄镜像层

---

## 2026-10-14：Nexus → ACR 链路切换，应用镜像首推成功

### 做了什么

1. `deploy/build-push.ps1` 改造：
   - Registry / 命名空间默认从 `deploy\.env` 读取（REGISTRY / NAMESPACE / REGISTRY_USER / REGISTRY_PASSWORD）
   - 检测到未登录时自动 `docker login`（password-stdin，密码不进进程列表）；已有登录态优先用 docker 凭证
   - 构建加 `--provenance=false --sbom=false`（原因见踩坑 #2）
   - 上游镜像改「候选源」机制：直连失败自动切南大镜像源；本地已有则跳过 pull（原判断逻辑有 bug 已修）
   - 项目目录无 git 仓库时，tag 回退为 `日期-时间`（原脚本直接崩溃）
2. 应用镜像构建推送成功：`.../agentrunner/agentscop-platform:20261014-133852`
   与 `20261014-134119`（同一镜像，digest `d74ee428…`，后者为重跑产物；可任选）
3. 新增 `deploy\.env`：本机 ACR 凭证 + 仓库参数（`.gitignore` 的 `.env` 规则已覆盖，勿入库）
4. 会话后清理：构建日志、废镜像（attestation 版 `20261014-132614`）、TEI 南大中转标签、
   外层 `d:\workspace\agentscop\.git` 空仓库（无提交/无远程，确认后删除）

### 踩坑记录（均已修复进脚本，后续复用时不用再踩）

| # | 症状 | 原因 | 解决 |
|---|---|---|---|
| 1 | 脚本启动即崩 `fatal: Needed a single revision` | 项目目录无 `.git`，外层误建空仓库劫持了 git | tag 回退时间戳；空仓库已删。**建议后续在项目内 `git init`，tag 恢复带短 sha** |
| 2 | push 被拒 `unknown manifest class for vnd.oci.empty.v1+json` | Docker Desktop（containerd 镜像存储）默认附加 OCI attestation 清单，ACR 个人版不认 | 构建加 `--provenance=false --sbom=false` |
| 3 | `pull redis:7` 连接超时 | CN 网络直连 Docker Hub 不通（TEI 走南大镜像所以正常） | 候选源机制：直连失败自动切 `docker.nju.edu.cn` |
| 4 | `deploy\.env` 凭证登录 401 | 实际固定密码与 `.env` 记录不一致（用户名 `aliyun5272623910` 无误） | 脚本优先用已存 docker 登录态；密码待改强密码后回填 `deploy\.env` |
| 5 | PowerShell `2>$null` 反而让脚本终止 | `$ErrorActionPreference='Stop'` 时 stderr 重定向会升级为终止错误 | 取 git 短哈希时临时降级 EAP |

### 待办（下次从这里继续）

1. 补推上游镜像：`.\deploy\build-push.ps1 -PushUpstream`（应用层已存在会秒过，只补 redis/qdrant/tei）
2. ACR 控制台把固定密码改为强密码 → 同步更新 `deploy\.env`
3. 服务器部署：按 DEPLOY-ALIYUN.md §1.3（预检/证书/目录）→ §3（上传/拉起/验证）
4. 数据迁移：新栈验收通过后另行立项（DEPLOY-ALIYUN.md §5）
