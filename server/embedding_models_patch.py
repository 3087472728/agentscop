# -*- coding: utf-8 -*-
# ============================================================
# 模块名: embedding_models_patch.py
# 用途:   修复 KB 创建时 embedding 模型清单错配——
#         框架 /knowledge_bases/embedding_models 端点对每个凭据都返回
#         OpenAI 内置静态清单（text-embedding-3-small/large），完全不看
#         凭据的 base_url。导致本地 bge 凭据（embed-local-8003）也显示
#         OpenAI 模型，用户选 1536 维后实际服务只出 512 维向量，
#         入库时报 "Credential not found"。
#
#         本 patch 替换该端点：对带自定义 base_url 的 openai 凭据，
#         动态拉取 {base_url}/models 获取真实模型清单（OpenAI 兼容格式，
#         条目可带 dimensions 扩展字段）；失败回退静态清单。
# 接口:   setup(app) —— 替换路由，启动时调用一次
# ============================================================
import asyncio
import datetime
import json
import logging
import os
import sqlite3
import time
import uuid

import httpx
from fastapi import Depends
from fastapi.routing import APIRoute

from agentscope.app._router._schema import (
    KbEmbeddingProvider,
    ListKbEmbeddingModelsResponse,
)
from agentscope.app.access import ResourceKind
from agentscope.app.deps import (
    get_current_user_id,
    get_knowledge_base_manager,
    get_resource_access_service,
)
from agentscope.credential import CredentialFactory
from agentscope.embedding import EmbeddingModelCard

logger = logging.getLogger("main.embedding_models")

from . import paths

REMOTE_FETCH_TIMEOUT = 10.0   # 拉取远端 /models 的超时（秒）
CACHE_TTL = 300.0             # 动态清单缓存有效期（秒）
OPENAI_OFFICIAL = "api.openai.com"

# 动态清单缓存: base_url -> (拉取时间, [EmbeddingModelCard])
_remote_cards_cache: dict[str, tuple[float, list[EmbeddingModelCard]]] = {}


def _db_credential_secrets() -> dict[str, tuple[str, str]]:
    """从数据库读取凭据的 (base_url, api_key)。

    CredentialView 对非 owner 用户会脱敏敏感字段（base_url 为 None），
    导致共享凭据无法触发动态拉取；这里直接读库拿到真实值，
    保证 owner 与共享用户看到一致的模型清单。
    """
    result: dict[str, tuple[str, str]] = {}
    try:
        con = sqlite3.connect(
            f"file:{paths.DB_PATH}?mode=ro", uri=True,
        )
        try:
            rows = con.execute("select id, payload from credentials").fetchall()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — 读库失败退回脱敏视图
        logger.warning("读取凭据表失败: %s", exc)
        return result
    for cid, payload in rows:
        try:
            data = (json.loads(payload) or {}).get("data") or {}
        except Exception:  # noqa: BLE001
            continue
        result[str(cid)] = (
            str(data.get("base_url") or "").strip().rstrip("/"),
            str(data.get("api_key") or "").strip(),
        )
    return result


# 本地 embedding 服务凭据的识别特征
_EMBED_URL_HINTS = ("127.0.0.1", "localhost", ":8003")


def _is_embed_credential(data: dict) -> bool:
    """判断某个凭据是否为「embedding 服务」凭据。"""
    if str(data.get("type") or "") != "openai_credential":
        return False
    name = str(data.get("name") or "").lower()
    url = str(data.get("base_url") or "").lower()
    if "embed" in name or "bge" in name:
        return True
    return any(h in url for h in _EMBED_URL_HINTS)


def _create_embed_credential_for(user_id: str) -> str | None:
    """为用户惰性创建一份 embedding 凭据（复制现有模板）。

    框架要求 KB 的 embedding 凭据与 KB 同属一个 owner，而系统里本地
    embed 服务通常只有一份凭据（属于管理员）。其他用户第一次建库前
    自动复制一份给他，避免「无凭据可选」或「选了不可用」。
    成功返回新凭据 id；已有 / 无模板 / 失败返回 None。
    """
    db_path = paths.DB_PATH
    try:
        con = sqlite3.connect(db_path, timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.warning("打开凭据表失败: %s", exc)
        return None
    try:
        rows = con.execute("select user_id, payload from credentials").fetchall()
        # 已有自己的 embedding 凭据则不重复创建（并发幂等保护）
        for owner, payload in rows:
            if str(owner) != user_id:
                continue
            try:
                data = (json.loads(payload) or {}).get("data") or {}
            except Exception:  # noqa: BLE001
                continue
            if _is_embed_credential(data):
                return None
        # 找模板：任意一个带 base_url 的 embedding 凭据
        template = None
        for _owner, payload in rows:
            try:
                data = (json.loads(payload) or {}).get("data") or {}
            except Exception:  # noqa: BLE001
                continue
            if _is_embed_credential(data) and data.get("base_url"):
                template = data
                break
        if template is None:
            logger.warning("未找到可复制的 embedding 凭据模板")
            return None
        new_id = uuid.uuid4().hex
        now = datetime.datetime.now().isoformat()
        new_data = {
            "id": new_id,
            "name": str(template.get("name") or "embed-local"),
            "type": "openai_credential",
            "api_key": str(template.get("api_key") or "x"),
            "base_url": template.get("base_url"),
        }
        con.execute(
            "insert into credentials (user_id, id, created_at, updated_at, payload)"
            " values (?,?,?,?,?)",
            (user_id, new_id, now, now,
             json.dumps({"data": new_data}, ensure_ascii=False)),
        )
        con.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("为用户 %s 创建 embedding 凭据失败: %s", user_id, exc)
        try:
            con.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    finally:
        con.close()
    logger.info("已为用户 %s 自动创建 embedding 凭据 %s", user_id, new_id)
    return new_id


def _builtin_cards(embedding_cls) -> dict[str, EmbeddingModelCard]:
    """内置静态清单按模型名索引（回退与维度对齐用）。"""
    return {c.name: c for c in embedding_cls.list_models()}


def _make_card(name: str, dimensions: int, builtin: dict) -> EmbeddingModelCard:
    """根据远端 /models 条目构造模型卡片。

    - 条目带 dimensions（本地 embed_server 扩展字段）→ 固定维度卡片，
      前端维度选择器会显示只读徽章，从根上杜绝维度错配
    - 不带 → 与内置同名卡片对齐维度；都没有则按 OpenAI 习惯
      默认 1536 + matryoshka 可选维度
    """
    if dimensions and dimensions > 0:
        return EmbeddingModelCard(
            name=name,
            label=name,
            dimensions=dimensions,
            supported_dimensions=None,
        )
    base = builtin.get(name)
    if base is not None:
        return base
    return EmbeddingModelCard(
        name=name,
        label=name,
        dimensions=1536,
        supported_dimensions=[1536, 1024, 768, 512, 256],
    )


async def _fetch_remote_cards(
    base_url: str,
    api_key: str,
    builtin: dict,
) -> list[EmbeddingModelCard] | None:
    """拉取 {base_url}/models 并转为卡片；失败返回 None（走回退）。"""
    cached = _remote_cards_cache.get(base_url)
    now = time.monotonic()
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=REMOTE_FETCH_TIMEOUT) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — 失败回退静态清单
        logger.warning("拉取 %s/models 失败: %s", base_url, exc)
        return None
    cards = []
    has_dimensioned = False
    for item in payload.get("data", []):
        if isinstance(item, dict):
            model_id = item.get("id")
            dims = item.get("dimensions")  # 本地 embed 服务的扩展字段
        else:
            model_id, dims = item, None
        if not model_id:
            continue
        d = int(dims or 0)
        if d > 0:
            has_dimensioned = True
        cards.append(_make_card(str(model_id), d, builtin))
    # 只有「至少一个模型声明了 dimensions」才视为 embedding 端点；
    # 否则（如智谱 chat 凭据的 /models 返回 glm 对话模型）回退静态清单，
    # 避免把对话模型误当作 embedding 模型列出。
    if not cards or not has_dimensioned:
        logger.warning(
            "%s/models 未返回带 dimensions 的 embedding 模型，回退静态清单", base_url,
        )
        return None
    _remote_cards_cache[base_url] = (now, cards)
    logger.info("动态拉取 %s embedding 模型: %s", base_url, [c.name for c in cards])
    return cards


async def _list_kb_embedding_models(
    user_id: str = Depends(get_current_user_id),
    access=Depends(get_resource_access_service),
    manager=Depends(get_knowledge_base_manager),
) -> ListKbEmbeddingModelsResponse:
    """替换后的 /knowledge_bases/embedding_models。

    与框架原实现的差异：openai_credential 且 base_url 指向非 OpenAI
    官方端点时，动态拉取该端点的真实模型清单。
    """
    policy = await manager.get_dimension_policy()
    credentials = await access.list_resource(user_id, ResourceKind.CREDENTIAL)
    # 真实凭据信息（含被脱敏的 base_url/api_key），按 credential_id 索引
    secrets = _db_credential_secrets()

    # 惰性补凭据：用户没有自己的 embedding 凭据时自动复制一份。
    # 框架要求 KB 的 embedding 凭据与 KB 同 owner，否则索引必失败；
    # 这样任何用户第一次建库都能直接选到可用凭据，无需人工配置。
    has_own_embed = any(
        str(getattr(c, "user_id", "") or "") == str(user_id)
        and _is_embed_credential(c.data or {})
        for c in credentials
    )
    if not has_own_embed and await asyncio.to_thread(
        _create_embed_credential_for, user_id,
    ):
        secrets = _db_credential_secrets()
        credentials = await access.list_resource(user_id, ResourceKind.CREDENTIAL)

    providers: list[KbEmbeddingProvider] = []
    for credential in credentials:
        data = credential.data or {}
        credential_type = data.get("type")
        if not credential_type:
            continue
        # 框架的 KB manager 走 owner-internal 路径：索引时按 KB 的 owner
        # 查凭据。跨用户的共享凭据虽然可见，但建库后会报
        # "Credential ... not found"。故这里只列出用户自己的凭据，
        # 保证「能选到的 = 能用的」。
        if str(getattr(credential, "user_id", "") or "") != str(user_id):
            continue
        credential_cls = CredentialFactory.get_credential_class(credential_type)
        if credential_cls is None:
            continue
        embedding_cls = credential_cls.get_embedding_model_class()
        if embedding_cls is None:
            continue

        builtin = _builtin_cards(embedding_cls)
        cards: list[EmbeddingModelCard] | None = None
        if credential_type == "openai_credential":
            # 优先用库里的真实值（共享用户看到的视图会脱敏 base_url）
            base_url, api_key = secrets.get(str(credential.id), ("", ""))
            if not base_url:
                base_url = str(data.get("base_url") or "").strip().rstrip("/")
                api_key = api_key or str(data.get("api_key") or "").strip()
            # 仅自定义端点动态拉取；OpenAI 官方走内置静态清单
            if base_url and api_key and OPENAI_OFFICIAL not in base_url:
                cards = await _fetch_remote_cards(base_url, api_key, builtin)
        if cards is None:
            cards = list(builtin.values())

        filtered = []
        for card in cards:
            projected = policy.filter_card(card)
            if projected is not None:
                filtered.append(projected)
        if not filtered:
            continue
        providers.append(
            KbEmbeddingProvider(credential=credential, models=filtered),
        )

    return ListKbEmbeddingModelsResponse(providers=providers, policy=policy)


def setup(app) -> None:
    """替换框架的 /knowledge_bases/embedding_models 路由。

    新版 FastAPI 把 include 进来的 router 包装为 _IncludedRouter，
    真正的 APIRoute 位于其 original_router.routes 中（相对路径，不带
    router prefix）。在原 router 内替换可继承 include 时的 prefix 与
    认证依赖，且路由版本号变化会使框架缓存自动失效。
    """
    from fastapi.routing import _IncludedRouter

    target = "/knowledge_bases/embedding_models"
    replaced = False
    for wrapper in app.router.routes:
        if not isinstance(wrapper, _IncludedRouter):
            continue
        router = wrapper.original_router
        # 用 '/knowledge_bases/chunkers' 特征路由确认这是 knowledge_base_router，
        # 避免误替换其他 router 下的同名路径
        paths = {getattr(r, "path", "") for r in router.routes}
        if target not in paths or "/knowledge_bases/chunkers" not in paths:
            continue
        for route in list(router.routes):
            if isinstance(route, APIRoute) and route.path == target:
                new_route = APIRoute(
                    path=route.path,
                    endpoint=_list_kb_embedding_models,
                    response_model=ListKbEmbeddingModelsResponse,
                    methods=route.methods,
                    name=route.name,
                    tags=route.tags,
                    summary="List KB embedding models (dynamic per credential)",
                )
                router.routes.remove(route)
                router.routes.append(new_route)
                replaced = True
                break
        if replaced:
            break
    if replaced:
        logger.info(
            "已替换路由 %s：自定义端点凭据改为动态拉取模型清单", target,
        )
    else:
        logger.warning("未找到 knowledge_base_router，跳过 embedding_models patch")
