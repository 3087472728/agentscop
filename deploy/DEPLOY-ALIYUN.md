# 部署链路（阿里云 ACR 版）：本机构建 → ACR → 服务器新栈

> 旧 Nexus 链路（150.223.194.46:31382）为团队项目所用，物料已移出本仓库，两者互不影响。
>
> **执行进度与实操流水见 [EXECUTION-LOG.md](EXECUTION-LOG.md)**
> （当前 2026-10-14：**服务器部署全部完成 ✅** — 3.1-3.4 四容器 healthy；
> 3.5 kb_smoke / validate 全绿；3.6 register/sync/embedding/model 四连完成，
> chat_smoke 实测 glm-5.3-flash 对话通过。仅剩浏览器人工验收 ⬜）。

## 0. 总览

```
Windows 本机                          阿里云 ACR（个人版·公网域名）            192.168.100.100
docker build  ──push 443──►  crpi-d92le52k5f114023.          ──pull 443──►  新栈 :6020 (HTTPS)
docker push                  cn-hangzhou.personal.cr.aliyuncs.com           agentscop-platform 项目
```

- 镜像前缀（公网域名；`-vpc` 域名仅 VPC 内 ECS 可用，忽略）：
  `crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com/<ns>/<image>:<tag>`
- 服务器三个在跑的服务，**一律不动**：
  | 服务 | 端口 | 说明 |
  |---|---|---|
  | agentrunner systemd `agentscope-api.service` | 127.0.0.1:8020 | 你的旧预研，后续迁数据的源 |
  | agentscope-v2 compose 栈 | 8005 | 公司栈（agentscope2-* 容器） |
  | 其他项目容器（TDengine/RocketMQ/memos…） | — | 勿误伤 |
- 新栈占用：**6020**（backend，唯一对外）+ 回环 6381(redis) / 6335(qdrant) / 19558(tei)
- 服务器目录：`/home/agentrunner/agentscope-official/`（与 v2 的目录并存）

## 1. 一次性准备

### 1.1 ACR 控制台（浏览器）
✅ 已完成（2026-10-14）：
1. 命名空间已建：`agentrunner`（下文 `<ns>` 即它）；「自动创建仓库」已开启
   （默认私有）——push 时四个仓库自动创建，无需手工逐个建
2. 「访问凭证」页已设固定密码；登录用户名 `aliyun5272623910`
   ⚠️ 改密码后须同步更新 `deploy\.env` 的 `REGISTRY_PASSWORD`

### 1.2 本机（Windows，Docker Desktop 运行中）
```powershell
docker login crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com
# 输入 1.1 的用户名/固定密码；验证登录态：
.\deploy\tools\docker-auth-check.ps1     # auths 里应出现 ACR 域名

# 凭证同时维护在 deploy\.env（REGISTRY_USER / REGISTRY_PASSWORD，.gitignore 已排除）：
# build-push.ps1 检测到未登录时用它自动登录；已有登录态时以 docker 凭证为准
```

### 1.3 服务器一次性准备（192.168.100.100，账号 `agentrunner`，ssh agentrunner@192.168.100.100）

> 实操请直接按**第 3 节的按序执行清单**走（与本节命令相同，第 3 节带勾选框和实际值）。
```bash
# 1) 公网可达性预检（公司内网若禁外网需先开通 443 出方向）
curl -sI --connect-timeout 8 https://crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com/v2/ | head -1   # 期望 HTTP/1.1 401（401=通，需登录）

# 2) 新栈端口必须空闲（8005/8020/老栈端口不在此列）
ss -tlnp | grep -E ':(6020|6381|6335|19558)\b' || echo "ports free"

# 3) docker 登录 ACR
docker login crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com

# 4) 部署目录（agentrunner 家目录，无需 sudo）
mkdir -p /home/agentrunner/agentscope-official/{workspaces,blobs,logs,certs}

# 5) HTTPS 自签名证书（SAN 必须含 IP）
cd /home/agentrunner/agentscope-official
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout certs/key.pem -out certs/cert.pem \
    -subj "/CN=192.168.100.100" -addext "subjectAltName=IP:192.168.100.100"
chmod 600 certs/key.pem

# 6) bge 权重复用确认（TEI 只读挂载宿主机，不进镜像）
ls /home/agentrunner/models/BAAI--bge-small-zh-v1.5/config.json 2>/dev/null && echo OK \
  || echo "权重缺失：从本机 models/bge/BAAI--bge-small-zh-v1.5/ 上传扁平 HF 文件"
```

## 2. 本机构建与推送

```powershell
cd d:\workspace\agentscop\agentscop-platform

# 首次：连上游镜像一起中转（服务器只依赖 ACR 可达，不连 Docker Hub/ghcr）
.\deploy\build-push.ps1 -PushUpstream

# 日常：只构建推送应用镜像
.\deploy\build-push.ps1
```

- Registry / 命名空间默认读 `deploy\.env`（REGISTRY / NAMESPACE），无需传参，也可 `-Namespace` 覆盖
- tag 规则：`日期-git短sha`；项目目录无 git 仓库时回退 `日期-时间`（如 `20261014-133852`）
- 需要 BuildKit；本机无需 pnpm（npmmirror + 清华 pypi 国内直连）
- 构建带 `--provenance=false --sbom=false`：ACR 个人版不认 containerd 镜像存储默认
  附加的 OCI attestation 清单（`vnd.oci.empty.v1+json`），不禁用会 push 被拒
- 上游镜像多源兜底：redis/qdrant 直连 Docker Hub 超时自动切南大镜像源
  （docker.nju.edu.cn），TEI 同理（ghcr.nju.edu.cn）；本地已有该镜像则跳过 pull
- bge 权重 / .env / workspaces / blobs / logs 被 dockerignore 排除，不进镜像
- push 大 blob 超时属正常，原命令重试即续传
- 脚本结束打印镜像全名 → 回填服务器 `.env` 的 `AGENTS_IMAGE`（及 *_REDIS/_QDRANT/_TEI）

## 3. 服务器部署（按序执行清单）

> 实际值已定（2026-10-14）：
> - 命名空间：`agentrunner`（ACR「自动创建仓库」已开启，四个仓库已全部推送完成）
> - 镜像前缀（下文写作 `$REG`，填写 `.env` 时须写全称）：
>   `crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com/agentrunner`
> - 应用镜像 tag：`20261014-134119`（`20261014-133852` 为同镜像双 tag，留作回滚）
> - 上游镜像：`$REG/redis:7`、`$REG/qdrant:v1.19.1`、`$REG/tei:cpu-1.8`
>
> 登录：`ssh agentrunner@192.168.100.100`（docker / compose 全部直接用该身份执行）。
> 1.3 执行过且仍有效的步骤可跳过；执行一项勾一项 ☐→☑。

### 3.0 预检（agentrunner 身份）

- ☐ ACR 公网可达（期望输出 `401`，401=通、需登录）：

```bash
curl -sI --connect-timeout 8 https://crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com/v2/ | head -1
```

- ☐ 新栈端口空闲（期望输出 `ports free`）：

```bash
ss -tlnp | grep -E ':(6020|6381|6335|19558)\b' || echo "ports free"
```

### 3.1 服务器初始化（agentrunner 身份）

- ☐ docker 登录 ACR（用户名 `aliyun5272623910` + 固定密码）：

```bash
docker login crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com
```

- ☐ 部署目录（agentrunner 家目录，无需 sudo）：

```bash
mkdir -p /home/agentrunner/agentscope-official/{workspaces,blobs,logs,certs}
```

- ☐ 自签 HTTPS 证书（SAN 必须含 IP）：

```bash
cd /home/agentrunner/agentscope-official
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout certs/key.pem -out certs/cert.pem \
    -subj "/CN=192.168.100.100" -addext "subjectAltName=IP:192.168.100.100"
chmod 600 certs/key.pem
```

- ☐ bge 权重复用确认（期望 `OK`；缺失则按提示从本机上传扁平 HF 文件）：

```bash
ls /home/agentrunner/models/BAAI--bge-small-zh-v1.5/config.json 2>/dev/null && echo OK \
  || echo "权重缺失：从本机 models/bge/BAAI--bge-small-zh-v1.5/ 上传扁平 HF 文件"
```

### 3.2 上传配置（Windows 本机执行）

```powershell
cd d:\workspace\agentscop\agentscop-platform
scp deploy\docker-compose.server.yml agentrunner@192.168.100.100:/tmp/
scp deploy\.env.server.example      agentrunner@192.168.100.100:/tmp/
```

服务器上放到位：

```bash
cd /home/agentrunner/agentscope-official
mv /tmp/docker-compose.server.yml /tmp/.env.server.example .
```

### 3.3 填写 .env（agentrunner 身份）

```bash
cp .env.server.example .env && vi .env && chmod 600 .env
```

逐项核对（其余保持模板默认）：

| 变量 | 值 |
|---|---|
| `AGENTS_IMAGE` | `$REG/agentscop-platform:20261014-134119` |
| `AGENTS_IMAGE_REDIS` | `$REG/redis:7` |
| `AGENTS_IMAGE_QDRANT` | `$REG/qdrant:v1.19.1` |
| `AGENTS_IMAGE_TEI` | `$REG/tei:cpu-1.8` |
| `AGENTS_BACKEND_PORT` | `6020`（模板默认已对） |
| `AGENTS_USERS` | `admin:<强密码>`（必改，首启种子管理员） |
| `ZAI_API_KEY` | 智谱 key（可选，PDF 视觉解析用） |

⚠️ 勿填 `20261014-135805`——那是本机构建脚本打印的占位 tag，未推送过。

（可选）`.env` 加一行 `COMPOSE_FILE=docker-compose.server.yml`，之后命令可省略 `-f`。

### 3.4 拉取并启动（agentrunner 身份）

- ☐ pull + up：

```bash
cd /home/agentrunner/agentscope-official
# 服务器 docker 20.10 无 compose 子命令，用独立二进制（v2.38 已装）；exec 加 -T
docker-compose -f docker-compose.server.yml pull     # 首次 4 个镜像，约数分钟
docker-compose -f docker-compose.server.yml up -d
docker-compose -f docker-compose.server.yml ps       # 等 backend / tei → healthy
```

### 3.5 验证（agentrunner 身份）

- ☐ 健康检查（期望 `401` = 存活，服务要求认证）：

```bash
curl -sk https://127.0.0.1:6020/health -u admin:<口令> -H "X-User-ID: admin" -o /dev/null -w "%{http_code}\n"
```

- ☐ 容器内冒烟三连：

```bash
docker-compose -f docker-compose.server.yml exec -T backend python scripts/validate_setup.py
docker-compose -f docker-compose.server.yml exec -T backend python scripts/chat_smoke.py --name 周报助手 --message "列出你的技能"
docker-compose -f docker-compose.server.yml exec -T backend python scripts/kb_smoke.py
```

- ☐ 浏览器：`https://192.168.100.100:6020`（自签证书点"继续前往"）→ 登录 → 对话 / 建知识库 / 传文档。

### 3.6 资产初始化（首次必做，幂等；agentrunner 身份）

- ☐ 依次执行：

```bash
docker-compose -f docker-compose.server.yml exec -T backend python scripts/register_agents.py
docker-compose -f docker-compose.server.yml exec -T backend python scripts/sync_skills.py
docker-compose -f docker-compose.server.yml exec -T backend python scripts/configure_model.py
docker-compose -f docker-compose.server.yml exec -T backend python scripts/configure_embedding.py
```

> **红线**：全程不碰 `agentscope2-*`（8005 公司栈）、8020 旧预研服务、TDengine/RocketMQ 等其他容器。
> **回滚** = `.env` 的 `AGENTS_IMAGE` 换回旧 tag → `up -d`，秒级，数据卷不动。

## 4. 升级 / 回滚（秒级，数据卷独立不丢）

```powershell
# 本机出新 tag
.\deploy\build-push.ps1 -Namespace <ns>
```
```bash
# 服务器：.env 的 AGENTS_IMAGE 换新 tag →
docker-compose -f docker-compose.server.yml pull && docker-compose -f docker-compose.server.yml up -d
# 回滚：AGENTS_IMAGE 改回旧 tag → up -d；绝不影响 8005 栈与 8020 旧服务
```

## 5. 数据迁移（部署验证通过后另行执行，本次不做）

源：旧预研服务（agentrunner systemd :8020，sqlite + 本地向量库 + file 角色表）
目标：新栈（redis + qdrant + tei）。届时单独立项：导出旧 data/、config/roles.json、
workspaces/blobs → 导入新栈（redis 角色表 + qdrant 重索引 + blobs/workspaces 落盘）。
旧服务在迁移验收前保持运行，随时可回退。

## 6. 故障排查速查

| 症状 | 处置 |
|---|---|
| 服务器 pull 401 / unauthorized | `docker login` ACR（agentrunner 身份）；确认用公网域名而非 -vpc |
| 服务器 docker 报 permission denied | 当前账号不在 docker 组：请管理员执行 `sudo usermod -aG docker agentrunner` 后重新登录（或换有 docker 权限的账号跑 docker/compose 部分） |
| 本机 push 报 unknown manifest class for vnd.oci.empty.v1+json | 旧脚本构建带了 attestation 清单；现脚本已加 `--provenance=false --sbom=false`，直接重跑 |
| 本机 pull redis/qdrant 报 dial tcp registry-1.docker.io 超时 | CN 网络直连 Docker Hub 不通；脚本自动切南大镜像源（docker.nju.edu.cn），或手动 `docker pull docker.nju.edu.cn/library/redis:7` 后重跑 |
| 本机 push 超时 | 重试即续传；确认 docker login 的是 ACR 而非旧 Nexus |
| 容器重启循环 "OS can't spawn worker thread" | compose 已配 `seccomp=unconfined`（老引擎限制），勿去掉 |
| tei unhealthy | `curl 127.0.0.1:19558/health`；宿主机权重目录须含扁平 HF 文件 |
| 后端 ModuleNotFoundError | 漏声明的依赖补进 requirements.txt 重新构建 |
| 管理脚本报 SSL: CERTIFICATE_VERIFY_FAILED | `.env` 置 `AGENTS_INSECURE_TLS=1` |
| 浏览器 401 | 用户须在 `AGENTS_USERS` 中；`X-User-ID` 与 Basic 用户一致 |
| 静态页 404/白屏 | 镜像内 `/app/web_ui/frontend/dist/index.html` 是否存在（构建日志 stage 1） |
| 端口被占 | `ss -tlnp` 核对 6020/6381/6335/19558，改 compose 端口映射 + `.env` 同步 |
| 管理脚本连接被掐断 RemoteDisconnected | 脚本默认 `http://` 打 HTTPS 端口；`.env` 须有 `AGENTSCOPE_API=https://localhost:6020`（2026-10-14 已补） |
| 管理脚本 401 Invalid or missing credentials | redis 角色模式首启只落 `roles.snapshot.json` 不落 `roles.json`，脚本认证读不到用户表；容器内执行 `cp /app/config/roles.snapshot.json /app/config/roles.json`（⚠️ 容器重建后需重做；彻底修复需让脚本/后端适配 redis 模式） |
| validate 报 cannot see own agent '个人助手' | 镜像内 `/app/registry.json` 带了开发期陈旧条目（agent_id 不存在于新栈）；从 registry 删除该条目，并回本地仓库清理后重新构建 |
