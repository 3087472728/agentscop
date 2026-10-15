# -*- coding: utf-8 -*-
# ============================================================
# 模块名: remote_models.py
# 用途:   动态模型列表（方案 B）——
#         agentscope 内置的模型下拉清单只来自包内 _openai_chat/_models/*.yaml
#         静态文件，不会读取凭据 base_url 下的模型。这里在启动后开一个后台
#         任务，定期遍历数据库里所有 openai_credential 凭据，请求其
#         base_url 的 /models 接口（OpenAI 兼容端点标准接口，如智谱 GLM），
#         把动态发现的模型合并进 OpenAIChatModel.list_models() 的返回值，
#         前端下拉框即可看到。
#         另有方案 A 兜底：glm-4.6 / glm-4.5 / glm-4.5-air / glm-4.5v 的
#         YAML 已直接放入包内 _models 目录，即使远端拉取失败也能看到。
# 接口:   setup(app, storage) —— 打补丁并注入后台刷新任务，启动时调用一次
# ============================================================
import asyncio
import base64
import contextlib
import copy
import logging
import re
import struct
import zlib

import httpx
from agentscope.model import ModelCard, OpenAIChatModel

REMOTE_MODEL_REFRESH_INTERVAL = 600   # 后台刷新间隔（秒）
REMOTE_MODEL_TIMEOUT = 10.0           # 拉取远端 /models 的超时（秒）

logger = logging.getLogger("main.remote_models")

# 包内置（含方案 A 兜底 YAML）的静态模型卡片，setup 时读取一次
_builtin_model_cards: list[ModelCard] = []
_builtin_model_names: set[str] = set()
# 同名快速查找：远端拉到的模型若命中内置清单，直接复用内置卡片的元数据
# （如 glm-4.5v 的图片输入能力、context_size 等，远端 /models 不返回这些）
_builtin_cards_by_name: dict[str, ModelCard] = {}

# 视觉模型名特征：glm-4.5v / glm-5v-turbo / glm-4v / qwen-vl 等。
# 远端 /models 只返回模型 id，没有模态信息，只能按命名惯例推断。
_VISION_NAME_PATTERN = re.compile(
    r"(\dv)|(\d\.\d?v)|(-vl)|vision|multimodal",
    re.IGNORECASE,
)
# 视觉模型的输入模态（与内置 glm-4.5v.yaml 保持一致）
_VISION_INPUT_TYPES = [
    "text/plain",
    "image/jpeg",
    "image/png",
    "image/webp",
]
# 纯文本模型的输入模态
_TEXT_INPUT_TYPES = ["text/plain"]

# 视觉能力实测缓存：model_name -> bool（每个模型只探测一次）
# 命名惯例不可靠（如 glm-5.3-flash 支持图片但名字里没有 v），
# 因此对动态发现的模型发一张极小图片实测。
_vision_probe_cache: dict[str, bool] = {}


def _build_probe_png() -> str:
    """构造一张 64x64 纯红 PNG 的 base64，用作视觉能力探针。

    只用标准库（zlib + struct），不依赖 PIL。
    """
    size = 64
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * size for _ in range(size))

    def _chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


_PROBE_IMAGE_B64 = _build_probe_png()


async def _probe_vision_support(
    client,
    base_url: str,
    api_key: str,
    model_name: str,
) -> bool:
    """实测模型是否接受图片输入（HTTP 200 即视为支持）。

    结果按模型名缓存，同一进程内每个模型只探测一次；网络异常时
    保守返回 False（按纯文本处理，避免前端放开后调用失败）。
    """
    cached = _vision_probe_cache.get(model_name)
    if cached is not None:
        return cached
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{_PROBE_IMAGE_B64}",
                        },
                    },
                ],
            }
        ],
        "max_tokens": 1,
    }
    ok = False
    try:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        ok = resp.status_code == 200
    except Exception as exc:  # noqa: BLE001 — 探测失败按不支持处理
        logger.debug("视觉能力探测异常(%s): %s", model_name, exc)
    _vision_probe_cache[model_name] = ok
    logger.info("视觉能力探测: %s -> %s", model_name, "支持图片" if ok else "纯文本")
    return ok

# 动态发现的远端模型卡片（由后台任务刷新，全局并集，供 list_models 用）
_remote_model_cards: list[ModelCard] = []

# 按凭据分组的远端模型：credential_id -> [ModelCard]
# 供 /model_groups/ 接口使用，让前端每个凭据只显示自己的模型
_remote_models_by_credential: dict[str, list[ModelCard]] = {}


def _make_model_card(
    name: str,
    context_size: int = 128000,
    output_size: int = 32768,
) -> ModelCard:
    """根据模型名构造通用 ModelCard。

    远端 /models 接口只返回模型 id 列表，没有上下文长度等元数据，
    因此这里使用默认值；thinking/voice 等参数因能力未知一律隐藏。
    """
    base_schema = OpenAIChatModel.Parameters.model_json_schema()
    properties = copy.deepcopy(base_schema.get("properties", {}))
    for key in (
        "thinking_enable",
        "thinking_budget",
        "thinking_mode",
        "thinking_display",
        "voice",
    ):
        properties.pop(key, None)
    if "max_tokens" in properties:
        properties["max_tokens"]["maximum"] = output_size
    # 按模型名推断输入模态：视觉模型（glm-4.5v / glm-5v-turbo 等）允许
    # 图片，其余按纯文本处理。前端据此启用或禁用附件按钮。
    input_types = (
        list(_VISION_INPUT_TYPES)
        if _VISION_NAME_PATTERN.search(name)
        else ["text/plain"]
    )
    return ModelCard(
        name=name,
        label=name,
        status="active",
        input_types=input_types,
        context_size=context_size,
        output_size=output_size,
        parameter_schema={
            "type": "object",
            "properties": properties,
            "required": base_schema.get("required", []),
        },
        parameters_overrides={},
    )


async def _refresh_remote_models_once(storage) -> None:
    from . import paths

    if paths.STORAGE_MODE == "redis":
        # 动态发现的凭据遍历走 SQL 私有表；Redis 后端仅使用静态
        # 模型目录（models/*.yaml），跳过后台发现。
        return
    """遍历全部 openai_credential 凭据，拉取各 base_url 下的模型清单。"""
    from sqlalchemy import select

    from agentscope.app.storage._sql._tables import CredentialRow

    async with storage._session() as sess:
        rows = (await sess.execute(select(CredentialRow))).scalars().all()

    by_credential: dict[str, list[ModelCard]] = {}
    # 参与对话模型发现的凭据类型：框架 OpenAI 兼容凭据 + 自定义智谱凭据
    # （ZhipuCredential 继承 OpenAICredential，payload 结构一致）
    chat_credential_types = {"openai_credential", "zhipu_credential"}
    for row in rows:
        # payload 是 CredentialRecord 的完整 dump，凭据字段在 data 子字典
        data = (row.payload or {}).get("data") or {}
        if data.get("type") not in chat_credential_types:
            continue
        # 跳过 embedding 专用凭据（如 embed-local-8003）：
        # 其模型是向量模型，不应出现在对话模型下拉里
        if "embed" in str(data.get("name") or "").lower():
            continue
        base_url = str(data.get("base_url") or "").strip().rstrip("/")
        api_key = str(data.get("api_key") or "").strip()
        # OpenAI 官方端点（或无 base_url）：模型清单包内已内置，
        # 直接按凭据填充，保证前端每个凭据都有归属模型
        if not base_url or "api.openai.com" in base_url:
            by_credential[str(row.id)] = list(_builtin_model_cards)
            continue
        if not api_key:
            continue
        try:
            async with httpx.AsyncClient(timeout=REMOTE_MODEL_TIMEOUT) as client:
                resp = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:  # noqa: BLE001 — 单个凭据失败不影响其他
            logger.warning("拉取 %s/models 失败: %s", base_url, exc)
            continue
        cards: list[ModelCard] = []
        dynamic_cards: list[ModelCard] = []   # 需要实测图片能力的动态模型
        for item in payload.get("data", []):
            model_id = item.get("id") if isinstance(item, dict) else item
            if not model_id:
                continue
            model_id = str(model_id)
            # 命中内置清单 → 直接复用内置卡片：远端 /models 不返回
            # input_types/context_size 等元数据，内置卡片更准确
            # （如 glm-4.5v 的图片输入能力）。此前是直接 continue 跳过，
            # 会导致该模型在本凭据分组里彻底看不到。
            builtin = _builtin_cards_by_name.get(model_id)
            if builtin is not None:
                cards.append(builtin)
                continue
            # 过滤非聊天模型（embedding / 重排 / TTS / 语音识别等）
            lowered = model_id.lower()
            if any(
                k in lowered
                for k in (
                    "embedding", "embed", "bge", "gte", "m3e", "text2vec",
                    "rerank", "whisper", "tts", "audio",
                )
            ):
                continue
            card = _make_model_card(model_id)
            cards.append(card)
            dynamic_cards.append(card)
        # 实测各动态模型的图片支持能力（并发；结果按模型名缓存，
        # 同一进程内每个模型只探测一次）。命名惯例不可靠，实测为准。
        if dynamic_cards:
            async with httpx.AsyncClient(timeout=REMOTE_MODEL_TIMEOUT) as probe_client:
                results = await asyncio.gather(
                    *(
                        _probe_vision_support(probe_client, base_url, api_key, c.name)
                        for c in dynamic_cards
                    )
                )
            for card, supports_vision in zip(dynamic_cards, results):
                card.input_types = (
                    list(_VISION_INPUT_TYPES)
                    if supports_vision
                    else list(_TEXT_INPUT_TYPES)
                )
        by_credential[str(row.id)] = cards

    # 按凭据分组保存（供 /model_groups/ 接口）
    _remote_models_by_credential.clear()
    _remote_models_by_credential.update(by_credential)

    # 全局并集（保持 list_models 的向后兼容）
    merged: dict[str, ModelCard] = {}
    for cards in by_credential.values():
        for card in cards:
            merged.setdefault(card.name, card)
    _remote_model_cards[:] = list(merged.values())
    logger.info("远端模型列表刷新完成，动态发现 %d 个模型", len(merged))


def get_models_by_credential() -> dict[str, list[ModelCard]]:
    """返回按凭据分组的动态模型：{credential_id: [ModelCard]}。"""
    return dict(_remote_models_by_credential)


# 已排队的「凭据变更后刷新」任务；未完成前不再重复排队（防抖）
_pending_refresh_task: asyncio.Task | None = None


def schedule_remote_models_refresh(storage) -> None:
    """凭据增删改后立即调度一次模型发现（防抖合并）。

    原先只靠 600 秒周期刷新，用户新添凭据后聊天页最长要等
    10 分钟才出现模型。这里在 upsert/delete 凭据后主动触发一次，
    延迟 1 秒执行以确保存储事务已提交。
    """
    global _pending_refresh_task
    if _pending_refresh_task is not None and not _pending_refresh_task.done():
        return  # 已有待执行的刷新，跳过

    async def _run() -> None:
        await asyncio.sleep(1.0)   # 等事务提交
        try:
            await _refresh_remote_models_once(storage)
        except Exception as exc:  # noqa: BLE001 — 后台任务永不中断
            logger.warning("凭据变更后刷新远端模型列表失败: %s", exc)

    try:
        _pending_refresh_task = asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        pass   # 无事件循环（如导入期），忽略


async def _refresh_remote_models_loop(storage) -> None:
    """后台循环：启动即刷新一次，之后按固定间隔刷新。"""
    while True:
        try:
            await _refresh_remote_models_once(storage)
        except Exception as exc:  # noqa: BLE001 — 后台任务永不中断
            logger.warning("刷新远端模型列表失败: %s", exc)
        await asyncio.sleep(REMOTE_MODEL_REFRESH_INTERVAL)


def _start_remote_model_refresher(storage) -> asyncio.Task:
    """启动后台刷新任务。"""
    return asyncio.create_task(_refresh_remote_models_loop(storage))


def setup(app, storage) -> None:
    """注入动态模型发现能力：lifespan 挂后台任务 + 补丁 list_models。

    必须在应用启动（uvicorn.run）之前调用一次。
    """
    global _builtin_model_cards, _builtin_model_names, _builtin_cards_by_name

    # 包内置静态模型卡片（含方案 A 兜底 YAML），补丁前读取一次
    _builtin_model_cards = OpenAIChatModel.list_models()
    _builtin_model_names = {card.name for card in _builtin_model_cards}
    _builtin_cards_by_name = {card.name: card for card in _builtin_model_cards}

    # 新版 FastAPI/Starlette 已移除 add_event_handler，这里包装应用原有的
    # lifespan：进入时启动后台刷新任务，退出时取消它。
    _original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _lifespan_with_model_refresher(a):
        # 必须在框架 lifespan 进入（存储 __aenter__ 建表完成）之后再
        # 启动后台任务：首次刷新立即查 credentials 表，抢跑会在全新
        # 库上撞 "no such table: credentials"。
        async with _original_lifespan(a):
            refresher_task = _start_remote_model_refresher(storage)
            try:
                yield
            finally:
                refresher_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await refresher_task

    app.router.lifespan_context = _lifespan_with_model_refresher

    # 替换 OpenAIChatModel.list_models：内置清单 + 远端动态清单去重合并。
    _original_list_models = OpenAIChatModel.list_models.__func__

    def _patched_list_models(cls, custom_yaml_dir=None) -> list[ModelCard]:
        if custom_yaml_dir is not None:
            # 显式指定自定义目录时走原实现
            return _original_list_models(cls, custom_yaml_dir=custom_yaml_dir)
        cards = list(_builtin_model_cards)
        for card in _remote_model_cards:
            if card.name not in _builtin_model_names:
                cards.append(card)
        return cards

    OpenAIChatModel.list_models = classmethod(_patched_list_models)

    # 凭据增删改后立即刷新模型发现（替换实例方法，框架路由持有同一实例）
    _orig_upsert = storage.upsert_credential

    async def _upsert_with_refresh(*args, **kwargs):
        result = await _orig_upsert(*args, **kwargs)
        schedule_remote_models_refresh(storage)
        return result

    storage.upsert_credential = _upsert_with_refresh  # type: ignore[method-assign]

    _orig_delete = storage.delete_credential

    async def _delete_with_refresh(*args, **kwargs):
        result = await _orig_delete(*args, **kwargs)
        schedule_remote_models_refresh(storage)
        return result

    storage.delete_credential = _delete_with_refresh  # type: ignore[method-assign]
