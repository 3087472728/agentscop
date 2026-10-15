
一、本地开发（docker compose，根目录 docker-compose.yml）
前置：cd d:\workspace\agentscop\agentscop-platform


【启动 / 停止】
  docker compose up -d          # 启动（Redis + 后端 8000 + WebUI 5173）
  docker compose down           # 停止（redis 数据卷保留）
  docker compose ps             # 看状态

【改代码后】（源码是 bind-mount，多数情况重启即可，不用 --build）
  docker compose restart server # 改了 Python 代码
  docker compose restart web    # 改了前端配置（vite.config）
  docker compose up -d --build  # 改了 Dockerfile / requirements.txt 依赖后重建

【日志排查】
  docker compose logs -f server # 后端日志
  docker compose logs -f web    # 前端日志

【端口冲突】
  之前用 .\start-all.ps1 起过原生进程（占 6379/8000/5173）时，compose 起不来，
  先跑一次：.\stop-all.ps1

【零依赖模式】（本地日常开发推荐，不用 compose）
  .\start-dev.ps1               # 后端 8020 + Vite 热更新，sqlite + 本地向量库
  详见 README.md「本地启动（零外部依赖）」



二、服务器发布流程（阿里云 ACR：Windows 本机构建 → 服务器换版）
参考：deploy/DEPLOY-ALIYUN.md（主链路按序清单）
自动化版：.\deploy\build-push.ps1（下文是其手动拆解）


架构：本机 docker build → push 阿里云 ACR 个人版（公网域名 443）→
服务器 192.168.100.100 从 ACR pull 换版（新栈 HTTPS :6020）。
服务器部署目录：/home/agentrunner/agentscope-official（登录身份 agentrunner）。

镜像前缀（下文写作 $REG）：
  crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com/agentrunner

【第 0 步 前提】
- 本机 Docker Desktop 运行中；docker login <ACR 公网域名>
  （用户名 aliyun5272623910 + 固定密码；凭据同时维护在 deploy\.env 的
  REGISTRY_USER / REGISTRY_PASSWORD；验证登录态：
  .\deploy\tools\docker-auth-check.ps1）
- 服务器登录：ssh agentrunner@192.168.100.100（docker / compose 全部直接用
  该身份执行；agentrunner 已加 docker 组）

【第 1 步 本机：构建镜像】（项目根目录执行，多阶段构建，前端在镜像内编译）
  cd d:\workspace\agentscop\agentscop-platform
  $tag = "20261015-manual1"          # tag 自定，build/push/服务器三处一致
  $AppImage = "$REG/agentscop-platform:$tag"
  docker build --provenance=false --sbom=false -f deploy\Dockerfile -t $AppImage .
  # 也可用约定 tag：$sha = git rev-parse --short HEAD; $tag = "yyyyMMdd-$sha"
  # 需要 BuildKit；本机无需 pnpm（npmmirror + 清华 pypi，国内直连可构建）；
  # --provenance=false --sbom=false 必带：ACR 个人版不认 OCI attestation 清单；
  # bge 权重/.env/workspaces 等被 Dockerfile.dockerignore 排除，不进镜像

【第 2 步 本机：推送到 ACR】（push 大 blob 超时属正常，原命令重试即续传）
  docker push $AppImage
  # 看到 digest: sha256:... = 成功
  # 上游镜像（redis/qdrant/tei）首次需中转：.\deploy\build-push.ps1 -PushUpstream

【第 3 步 服务器：验证 tag 存在】（ACR 匿名 curl 会 401，用 docker pull 验证）
  ssh agentrunner@192.168.100.100
  docker pull $REG/agentscop-platform:<tag>
  # Downloaded = 存在；manifest unknown = tag 不存在，回第 1 步

【第 4 步 服务器：换 .env 并拉起】（agentrunner 身份）
  cd /home/agentrunner/agentscope-official
  sed -i 's|^AGENTS_IMAGE=.*|AGENTS_IMAGE='"$REG"'/agentscop-platform:新TAG|' .env
  grep AGENTS_IMAGE .env             # 核对（勿留"新TAG"占位符，会 invalid reference format）
  docker-compose -f docker-compose.server.yml pull && docker-compose -f docker-compose.server.yml up -d
  docker-compose -f docker-compose.server.yml ps   # 等 backend / tei → healthy
  # 注意：该机 docker 20.10 无 compose 子命令，用独立二进制 docker-compose，exec 加 -T

【第 5 步 验证】
  curl -sk https://127.0.0.1:6020/health -u admin:<口令> -H "X-User-ID: admin" -o /dev/null -w "%{http_code}\n"
  浏览器 https://192.168.100.100:6020（自签名证书点"继续前往"）
  改动含智能体/skill 定义时补跑（幂等）：
  docker-compose -f docker-compose.server.yml exec -T backend python scripts/register_agents.py
  docker-compose -f docker-compose.server.yml exec -T backend python scripts/sync_skills.py

【回滚】（秒级，数据卷独立不丢）
  sed 把 AGENTS_IMAGE 改回旧 tag → docker-compose -f docker-compose.server.yml up -d

【备选：本机 daemon 出网故障时】
  构建脚本已带南大镜像源兜底（docker.nju.edu.cn / ghcr.nju.edu.cn）；
  仍拉不到基础镜像时，把构建搬到能出网的机器上做后 push 到 ACR。

【常见坑】
- 红线：服务器上 agentscope2-*（8005 公司栈）、8020 旧预研 systemd 服务、
  TDengine/RocketMQ 等其他容器一律不动
- .env Permission denied → .env 属主应是 agentrunner，chmod 600
- invalid reference format:...新TAG → sed 没替换占位符，填真实 tag
- pull 报 manifest unknown / 401 → ACR 里没这个 tag 或未 docker login
- 本机 push 报 unknown manifest class for vnd.oci.empty.v1+json
  → 构建没带 --provenance=false --sbom=false
- 容器重启循环 "OS can't spawn worker thread" → compose 已配
  seccomp=unconfined（老引擎限制），勿去掉
- 管理脚本报 SSL: CERTIFICATE_VERIFY_FAILED → .env 置 AGENTS_INSECURE_TLS=1
- 管理脚本 RemoteDisconnected → .env 须有 AGENTSCOPE_API=https://localhost:6020
