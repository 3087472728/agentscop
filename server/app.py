# -*- coding: utf-8 -*-
"""智能体服务装配层。

遵循官方 ``examples/agent_service/main.py`` 的架构规范：框架能力全部
通过 ``create_app`` 的扩展点参数注入，业务扩展以「官方代码零改动」的
方式叠加。

合并来源
========
- agentscope_extra-master/server/main.py（骨架：RBAC 认证、按用户工作区、
  PDF 视觉解析、插图路由、单端口托管）
- agent-new/server/app_factory.py + main.py（平台能力：动态模型发现、
  模型分组、KB 重索引/凭据回退、技能可见性、会话权限/重放、工具卸载、
  僵死自愈、候选词端点、内部/GitHub 技能市场、.doc 解析）

启动方式（项目根目录）::

    python main.py            # 默认 sqlite + 本地 Qdrant，零外部依赖

生产/容器部署::

    AGENTS_STORAGE=redis AGENTS_QDRANT_MODE=url python main.py
    （Redis / Qdrant / TEI 由 docker-compose.yml 提供）

环境变量见 server/paths.py 模块文档与本目录 .env.example。
"""
import os
import secrets as _secrets
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from . import paths

# 部署端点。默认值与容器部署保持一致（backend 8000、Redis 6379、
# Qdrant 6333、TEI 19556）。本地开发机的默认端口由 start-dev.ps1 /
# start-all.ps1 设为 8020 —— 本机 127.0.0.1:8000 常被开发工具
# （CodeBuddy 等）占用，localhost:8000 会打到 IDE 服务上。容器/服务
# 器部署通过 .env 覆盖（见 deploy/DEPLOY-ALIYUN.md）。
BACKEND_PORT = int(os.getenv("AGENTS_BACKEND_PORT", "8000"))
REDIS_HOST = os.getenv("AGENTS_REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("AGENTS_REDIS_PORT", "6379"))
QDRANT_URL = os.getenv("AGENTS_QDRANT_URL", "http://localhost:6333").rstrip("/")
TEI_BASE_URL = os.getenv("TEI_BASE_URL", "http://localhost:19556/v1").rstrip("/")
# TLS（自签单端口部署）：两者都设置时 uvicorn 以 HTTPS 监听。
SSL_CERTFILE = os.getenv("AGENTS_SSL_CERTFILE", "").strip()
SSL_KEYFILE = os.getenv("AGENTS_SSL_KEYFILE", "").strip()

# 内部服务必须绕过系统代理：httpx 系 SDK（qdrant-client、openai）只认
# WinINET 代理（如 Clash Verge）而不认其 localhost 例外规则，会把每个
# 127.0.0.1 请求变成经代理进程的 502。compose 服务名同理加入。
_no_proxy = os.environ.get("NO_PROXY", "")
_qdrant_host = urlsplit(QDRANT_URL).hostname
_tei_host = urlsplit(TEI_BASE_URL).hostname
for _host in ("localhost", "127.0.0.1", REDIS_HOST, _qdrant_host, _tei_host):
    if _host and _host not in _no_proxy.split(","):
        _no_proxy = f"{_no_proxy},{_host}" if _no_proxy else _host
os.environ["NO_PROXY"] = _no_proxy
os.environ["no_proxy"] = _no_proxy

# paths.py 已按 STORAGE_MODE 预设了 AGENTS_ROLES_STORE（sqlite→file，
# redis→redis），必须在导入 roles_store 之前生效 —— 因此上面的 import
# 顺序不可调整。

import uvicorn  # noqa: E402
from fastapi.middleware import Middleware  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from agentscope.app import create_app, SubAgentTemplate  # noqa: E402
from agentscope.app.channel import (  # noqa: E402
    DingTalkChannel,
    DiscordChannel,
    FeishuChannel,
)
from agentscope.app.hub import ClawSkillHub, GitHubMCPHub  # noqa: E402
from agentscope.app.message_bus import InMemoryMessageBus  # noqa: E402
from agentscope.app.rag.blob_store import LocalBlobStore  # noqa: E402
from agentscope.app.rag.knowledge_base_manager import (  # noqa: E402
    CollectionPerKbManager,
)
from agentscope.mcp import MCPClient, StdioMCPConfig, HttpMCPConfig  # noqa: E402
from agentscope.middleware import AgenticMemoryMiddleware, MiddlewareBase  # noqa: E402
from agentscope.permission import PermissionContext, PermissionMode  # noqa: E402
from agentscope.rag import (  # noqa: E402
    ApproxTokenChunker,
    ExcelParser,
    PPTParser,
    QdrantStore,
    TextParser,
    WordParser,
)
from agentscope.workspace import WorkspaceBase  # noqa: E402

# ── 业务扩展模块（相对导入，作为 server 包的一部分）────────────────
from .auth import install_basic_auth  # noqa: E402
from .doc_parser import DocParser  # noqa: E402
from .embedding_models_patch import setup as setup_embedding_models  # noqa: E402
from .chat_uploads_router import setup as setup_chat_uploads  # noqa: E402
from .figures_router import figures_router  # noqa: E402
from .github_skill_hub import GitHubSkillHub  # noqa: E402
from .internal_skill_hub import InternalSkillHub  # noqa: E402
from .kb_credential_fallback_patch import setup as setup_kb_credential_fallback  # noqa: E402
from .kb_reindex_api import setup as setup_kb_reindex  # noqa: E402
from .model_groups_api import setup as setup_model_groups  # noqa: E402
from .model_policy import (  # noqa: E402
    LocalBgeCredential,
    ZhipuCodingPlanCredential,
)
from .pdf_figures_parser import PdfWithFiguresParser  # noqa: E402
from .per_user_workspace import PerUserLocalWorkspaceManager  # noqa: E402
from .quick_prompts_api import setup as setup_quick_prompts  # noqa: E402
from .remote_models import setup as setup_remote_models  # noqa: E402
from .role_access_policy import RoleBasedAccessPolicy  # noqa: E402
from .roles_admin_api import setup as setup_roles_admin  # noqa: E402
from .roles_store import bind_storage as roles_store_bind_storage  # noqa: E402
from . import roles_store  # noqa: E402
from .session_permission_patch import setup as setup_session_permission  # noqa: E402
from .session_replay_patch import setup as setup_session_replay  # noqa: E402
from .skill_visibility import setup as setup_skill_visibility  # noqa: E402
from .stuck_run_guard import setup as setup_stuck_run_guard  # noqa: E402
from .tool_offload_patch import setup as setup_tool_offload  # noqa: E402
from .zhipu_credential import ZhipuCredential  # noqa: E402

# ── 存储后端 ─────────────────────────────────────────────────────
# sqlite（默认）：AsyncSQLAlchemyStorage 单文件库，零外部服务；
# redis：生产/容器部署，配合 docker-compose.yml。
if paths.STORAGE_MODE == "redis":
    from agentscope.app.message_bus import RedisMessageBus
    from agentscope.app.storage import RedisStorage

    storage = RedisStorage(
        host=os.getenv("REDIS_HOST", REDIS_HOST),
        port=int(os.getenv("REDIS_PORT", REDIS_PORT)),
    )
    message_bus = RedisMessageBus(
        host=os.getenv("REDIS_HOST", REDIS_HOST),
        port=int(os.getenv("REDIS_PORT", REDIS_PORT)),
    )
else:
    from agentscope.app.storage import AsyncSQLAlchemyStorage

    storage = AsyncSQLAlchemyStorage(
        paths.SQLITE_URL,
        engine_kwargs={"connect_args": {"timeout": 60}},
    )
    message_bus = InMemoryMessageBus()

# ── 下载链接签名密钥 ─────────────────────────────────────────────
# 持久化到 config/download_secret.key，保证重启后历史下载链接不失效。
_dl_secret_path = os.path.join(paths.CONFIG_DIR, "download_secret.key")
try:
    with open(_dl_secret_path, "r", encoding="utf-8") as _f:
        DOWNLOAD_SECRET = _f.read().strip()
    if not DOWNLOAD_SECRET:
        raise FileNotFoundError
except FileNotFoundError:
    DOWNLOAD_SECRET = _secrets.token_urlsafe(32)
    os.makedirs(paths.CONFIG_DIR, exist_ok=True)
    with open(_dl_secret_path, "w", encoding="utf-8") as _f:
        _f.write(DOWNLOAD_SECRET)

# ── 共享技能母本（skills/<dir>/SKILL.md）─────────────────────────
# 播种到每个新工作区的 skills/.seed 模板；母本更新通过
# scripts/sync_skills.py 传播。目录中无 SKILL.md 的子目录（如 _template）
# 不会被装配。
_shared_skills_dir = paths.SKILLS_DIR
shared_skill_paths = (
    [
        os.path.join(_shared_skills_dir, name)
        for name in sorted(os.listdir(_shared_skills_dir))
        if os.path.isfile(os.path.join(_shared_skills_dir, name, "SKILL.md"))
    ]
    if os.path.isdir(_shared_skills_dir)
    else []
)

default_mcps: list[MCPClient] = [
    MCPClient(
        name="browser-use",
        mcp_config=StdioMCPConfig(
            command="npx",
            args=["@playwright/mcp@latest"],
        ),
        is_stateful=True,
    ),
]

if os.getenv("AMAP_API_KEY"):
    default_mcps.append(
        MCPClient(
            name="amap",
            mcp_config=HttpMCPConfig(
                url=f"https://mcp.amap.com/mcp?key="
                f"{os.environ['AMAP_API_KEY']}",
            ),
            is_stateful=False,
        ),
    )

# ── 向量库 ───────────────────────────────────────────────────────
# path 模式：进程内本地文件库（data/qdrant_data），零容器；
# url 模式：Qdrant 服务端容器（compose），命名卷跨重启持久化。
if paths.QDRANT_MODE == "path":
    vector_store = QdrantStore(path=paths.QDRANT_DIR)
else:
    vector_store = QdrantStore(url=QDRANT_URL)


async def longterm_memory_factory(
    user_id: str,
    agent_id: str,
    session_id: str,
    workspace: WorkspaceBase,
) -> list[MiddlewareBase]:
    """挂载 Markdown 文件形式的长期记忆，存放在会话工作区之下，
    因此无论绑定哪个后端都能访问到。"""
    del user_id, agent_id, session_id
    return [
        AgenticMemoryMiddleware(
            workdir=workspace.workdir,
            backend=workspace.get_backend(),
        ),
    ]


app = create_app(
    storage=storage,
    message_bus=message_bus,
    workspace_manager=PerUserLocalWorkspaceManager(
        # 工作区数据存放在本项目 workspaces/ 下；管理器把每个工作目录
        # 收窄到 workspaces/<agent>/users/<user>，使记忆、产物和生成
        # 文件按用户隔离。
        basedir=paths.WORKSPACES_DIR,
        default_mcps=default_mcps,
        skill_paths=shared_skill_paths,
    ),
    # 凭据类型注册（UI「新建凭据」下拉可选项）：
    # - Zhipu Coding Plan：模型目录仅含套餐模型（server/model_policy.py
    #   + models/*.yaml），对话模型默认 glm-5.3-flash；embedding 目录
    #   刻意为空（套餐端点只服务对话）。
    # - Local BGE (TEI)：唯一的 embedding 提供方，指向本地 TEI 容器
    #   （models/bge/）。本地无容器时可改指 embed_server.py（:
    #   8003/v1，sentence-transformers 实现，OpenAI 兼容）。
    # - Zhipu（OpenAI 兼容）：通用智谱开放平台凭据，支持任意
    #   base_url 的 OpenAI 兼容端点。
    extra_credentials=[ZhipuCodingPlanCredential, LocalBgeCredential, ZhipuCredential],
    # 资源可见性策略："属主决定" RBAC ——
    # admin 属主 = 公开（知识库/凭证对所有人只读，智能体由
    # config/roles.json 中按用户的 user_agents 白名单把关），
    # 用户属主 = 私有（仅属主本人和 admin 可见）。
    resource_access_policy=RoleBasedAccessPolicy(),
    # 下载签名密钥（文件/图片下载链接防篡改）
    download_secret=DOWNLOAD_SECRET,
    # 知识库特性。CollectionPerKbManager 每个知识库一个 collection，
    # 任意 embedding 维度均可。
    knowledge_base_manager=CollectionPerKbManager(
        storage=storage,
        vector_store=vector_store,
    ),
    # 上传文件（知识库文档等）落盘目录：显式绝对路径，不随启动
    # 目录漂移。
    blob_store=LocalBlobStore(root_dir=paths.BLOBS_DIR),
    # 创建知识库时用户可选的分块器类；所选类型和参数固定在该库上。
    knowledge_chunkers=[ApproxTokenChunker],
    # 知识库上传解析器。PDF 走视觉转写解析器（内嵌位图——含 JBIG2
    # 扫描件——由视觉模型描述后注入标题节）。Word/PPT 保持
    # include_image=False：其内嵌图片会以 DataBlock 节出现，纯文本
    # embedding 模型（bge-small-zh-v1.5）无法索引。老版 .doc 经
    # LibreOffice 转换（未安装则解析该类型时报错，其余类型不受影响）。
    knowledge_parsers=[
        TextParser(),
        PdfWithFiguresParser(),
        WordParser(include_image=False),
        DocParser(),
        ExcelParser(),
        PPTParser(include_image=False),
    ],
    # /hub 下的资源市场。MCP：GitHub 官方注册表；技能：内部技能库
    # （server/internal_skills/）+ ClawHub 官方市场 + skills.sh
    # （GitHub）。Hub 自身不需要凭据。
    mcp_hubs=[GitHubMCPHub()],
    skill_hubs=[
        InternalSkillHub(),
        ClawSkillHub(api_token=os.getenv("CLAWHUB_API_TOKEN")),
        GitHubSkillHub(),
    ],
    # 自定义子智能体模板：explorer（只读探索型）
    custom_subagent_templates=[
        SubAgentTemplate(
            type="explorer",
            description=(
                "Read-only agents specialized in exploration tasks. It can "
                "read files but cannot modify, create, or delete them. Use "
                "this agent type when you need to investigate the codebase, "
                "understand its structure, or gather information from files "
                "to support planning—without making any changes."
            ),
            system_prompt_template="""You are {member_name}, an explorer \
agent in team '{team_name}' led by {leader_name}.

Team purpose: {team_description}

Your role: {member_description}

## Responsibilities
- Complete the exploration tasks assigned by the team leader.
- You are read-only: you may inspect files and the codebase, but you must \
never modify, create, or delete anything.

## Reporting
- Always report the task result back to {leader_name} using the TeamSay \
tool, whether the task succeeds or fails.
- Keep your private reasoning private; only share conclusions and findings \
that the leader needs.

Note: `TeamSay` is your ONLY channel to communicate with {leader_name} and \
the other team members. Any other output you produce is invisible to them, \
so anything you want them to see MUST be sent through `TeamSay`.""",
            permission_context=PermissionContext(
                # 只读
                mode=PermissionMode.EXPLORE,
            ),
        ),
    ],
    # 长期记忆。默认的按智能体工作区隔离使记忆在同一智能体的
    # 多次会话之间延续。
    extra_agent_middlewares=longterm_memory_factory,
    extra_middlewares=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ],
    channels=[
        DingTalkChannel,
        DiscordChannel,
        FeishuChannel,
    ],
)

# ─────────────────────────────────────────────────────────────────
# 业务能力装配（均注册在 create_app 之后，路由与官方 API 并存）
# ─────────────────────────────────────────────────────────────────

# 1) HTTP Basic 认证：要求每个解析用户身份的端点都携带 Basic 凭证。
#    用户表事实源：redis 模式 = Redis roles:* 键族；file 模式 =
#    config/roles.json（随 AGENTS_ROLES_STORE 切换，见 paths.py）。
install_basic_auth(app)

# 2) /roles 管理 API（GET/POST，仅 admin）。必须在 install_basic_auth
#    之后注册，使其 Depends(get_current_user_id) 的强制校验走 Basic
#    认证验证器。
setup_roles_admin(app, storage)

# 3) 用户表 store 装配与生命周期（启动自检序列：PING → INFO 查 AOF →
#    补偿队列重试 → 首启种子导入 → 快照预热）。新版 Starlette 移除了
#    add_event_handler，改为包装框架既有 lifespan：store 的 startup 在
#    框架 lifespan 进入（连接就绪）之后执行，shutdown 在框架退出前
#    （连接仍可用）执行。file 回滚模式无 startup/shutdown，自动跳过。
roles_store_bind_storage(storage)
_roles_store_inst = roles_store.get_store()
if hasattr(_roles_store_inst, "startup") or hasattr(_roles_store_inst, "shutdown"):
    _base_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _roles_store_lifespan(a):
        async with _base_lifespan(a):
            if hasattr(_roles_store_inst, "startup"):
                await _roles_store_inst.startup()
            try:
                yield
            finally:
                if hasattr(_roles_store_inst, "shutdown"):
                    await _roles_store_inst.shutdown()

    app.router.lifespan_context = _roles_store_lifespan

# 4) 候选词端点（GET /agent/{agent_id}/quick-prompts）：config/
#    quick_prompts.json + 动态占位符插值（config/dynamic_sources.json）。
setup_quick_prompts(app)

# 5) 模型分组查询（GET /model_groups）：按凭据类型聚合模型清单。
setup_model_groups(app)

# 6) 远端模型动态发现：后台任务按凭据 base_url 拉 /models 并缓存。
setup_remote_models(app, storage)

# 7) KB embedding 模型目录按凭据过滤 + 缺省凭据自动创建。
setup_embedding_models(app)

# 8) 知识库文档重索引端点（embedding 模型/凭据更换后使用）。
setup_kb_reindex(app)

# 9) KB 凭据回退：embedding 凭据不可见时向公共凭据回退。
setup_kb_credential_fallback(app)

# 10) 技能按角色可见（admin 全部 / leader 本团队 / member 本人）。
setup_skill_visibility(storage)

# 11) 新会话默认权限模式 accept_edits（覆盖框架默认 explore，按
#     角色 admin=accept_edits / 其他=explore 可在模块内调整）。
setup_session_permission(app, storage)

# 12) 会话事件重放上限 1000 → 100000（长会话换端不丢历史）。
setup_session_replay()

# 13) 工具卸载阈值 10s → 45s，超时工具转为后台任务并推送事件。
setup_tool_offload()

# 14) 僵死会话自愈看门狗（chat 排队超时标记 + 上报通知）。
setup_stuck_run_guard(app, storage)

# 15) 插图静态路由（/kb-images，无鉴权；KB 内嵌图片的引用前缀）。
app.include_router(figures_router)

# 16) 聊天图片上传/回显路由 + formatter 相对 URL 解析补丁
#     （POST /chat_uploads/ 上传，GET /chat_uploads/{fn} 回显）。
setup_chat_uploads(app)

# ── Web UI 由本进程托管（单端口：API + 前端同源）─────────────────
# 两种互斥模式（导入时确定）：
# - Dev（start-all/start-dev 设置 AGENTS_DEV_FRONTEND_URL=<vite 地址>）：
#   所有非 API 路径反向代理到 Vite 开发服务器——资源走 HTTP、HMR 走
#   WebSocket。前端改动经本端口热更新，dist/ 是否存在无关紧要。
# - Serve（容器/服务器部署，未设该变量）：托管构建产物 dist/，
#   SPA fallback，与生产部署完全一致。
# 两种模式下 catch-all 都注册在 create_app 之后，显式 API 路由（以及
# /docs、/openapi.json）优先匹配。静态路由不声明用户依赖、不做鉴权
# ——浏览器 <script>/<link> 标签无法携带请求头，SPA 会为自己的
# fetch 调用自行鉴权。
_dist_dir = os.path.join(paths.ROOT, "web_ui", "frontend", "dist")
_dev_frontend = os.getenv("AGENTS_DEV_FRONTEND_URL", "").strip().rstrip("/")

if _dev_frontend:
    import asyncio

    import httpx
    import websockets
    from fastapi import Request, WebSocket
    from fastapi.responses import JSONResponse, Response

    # Dev 资源只会访问本地 Vite 进程：绝不让系统代理拦截这些调用
    # （design.md 14.3-2 教训）。
    _vite = httpx.AsyncClient(base_url=_dev_frontend, trust_env=False)
    _HOP_HEADERS = frozenset(
        (
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
            "content-length",
            "host",
        )
    )

    @app.api_route(
        "/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False
    )
    async def _dev_frontend_proxy(request: Request, full_path: str) -> Response:
        """Reverse-proxy non-API paths to the Vite dev server."""
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP_HEADERS
        }
        # 原样透传 query 是必要的：vite 模块 URL 携带语义化查询串
        # （".svg?react" 选择 svgr 转换）——丢弃会让 vite 退化为普通
        # URL 导出，React 崩溃 "createElement: invalid tag name"。
        target = "/" + full_path
        if request.url.query:
            target += "?" + request.url.query
        try:
            upstream = await _vite.request(
                request.method,
                target,
                headers=headers,
            )
        except httpx.HTTPError:
            return JSONResponse(
                {
                    "detail": (
                        f"Vite dev server unreachable at {_dev_frontend} "
                        "(start-all.ps1 starts it; or 'pnpm dev' in web_ui/)"
                    )
                },
                status_code=502,
            )
        resp_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in _HOP_HEADERS
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=resp_headers,
        )

    @app.websocket("/{full_path:path}")
    async def _dev_frontend_ws(websocket: WebSocket, full_path: str) -> None:
        """Pipe HMR (and any other) WebSockets through to Vite."""
        target = _dev_frontend.replace("http", "ws", 1) + "/" + full_path
        if websocket.url.query:
            target += "?" + websocket.url.query
        await websocket.accept()
        try:
            async with websockets.connect(target) as upstream:

                async def client_to_upstream() -> None:
                    while True:
                        msg = await websocket.receive()
                        if msg["type"] == "websocket.disconnect":
                            return
                        if msg.get("text") is not None:
                            await upstream.send(msg["text"])
                        elif msg.get("bytes") is not None:
                            await upstream.send(msg["bytes"])

                async def upstream_to_client() -> None:
                    async for data in upstream:
                        if isinstance(data, str):
                            await websocket.send_text(data)
                        else:
                            await websocket.send_bytes(data)

                await asyncio.gather(
                    client_to_upstream(),
                    upstream_to_client(),
                    return_exceptions=True,
                )
        except Exception:
            pass  # dev-only convenience: any teardown path is fine
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

elif os.path.isdir(_dist_dir):
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _assets_dir = os.path.join(_dist_dir, "assets")
    if os.path.isdir(_assets_dir):
        app.mount(
            "/assets", StaticFiles(directory=_assets_dir), name="assets"
        )

    _index_html = os.path.join(_dist_dir, "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        """Serve real files from dist; anything else gets index.html
        (SPA client-side routing). Known trade-off of the catch-all:
        a misspelled API path returns the SPA HTML instead of 404."""
        candidate = os.path.normpath(os.path.join(_dist_dir, full_path))
        if candidate.startswith(_dist_dir + os.sep) and os.path.isfile(
            candidate
        ):
            return FileResponse(candidate)
        return FileResponse(_index_html)
