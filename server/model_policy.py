# -*- coding: utf-8 -*-
"""智谱（Zhipu）Coding Plan 的凭证类型。

通过 ``create_app(extra_credentials=[...])`` 注册本类后，会新增一个
专属提供方，其模型目录仅限 Coding Plan 套餐内包含的模型（见
``models/*.yaml``）。平台里所有模型选择器（对话 / 定时任务 / 渠道）
的选项都读自 ``list_models()``，因此：

- 管理员添加一条此类型的凭证后，每个用户的会话会自动默认到套餐
  模型（UI 自动选中第一个可用模型）；
- 套餐外的模型在任何地方都不会出现、无法选择。

运行时构造不变：``get_chat_model_class`` 继承自
``OpenAICredential``，对话模型类仍是 ``OpenAIChatModel``，指向
Coding Plan 的 OpenAI 兼容端点。
"""
from pathlib import Path
from typing import Any, Literal, Type

from pydantic import ConfigDict

from agentscope.credential import OpenAICredential
from agentscope.embedding import OpenAIEmbeddingModel
from agentscope.model import OpenAIChatModel

#: 本项目自带的模型卡片（每个套餐模型一份 YAML）。
PLAN_MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

#: Local embedding model cards + weights (Git LFS), served by the TEI
#: container (see start-all.ps1: agentscope-tei on 127.0.0.1:19556).
BGE_MODELS_DIR = PLAN_MODELS_DIR / "bge"


class ZhipuPlanChatModel(OpenAIChatModel):
    """模型目录仅限套餐内模型的对话模型类。

    ``/model`` 路由（以及 ``get_model`` 中的运行时卡片查找）通过
    ``credential.get_chat_model_class().list_models()`` 解析候选
    模型 —— 只覆写凭证的 ``list_models`` 不够，对话模型类才是
    模型目录的真正来源。
    """

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ):
        """只呈现套餐内模型，由本地 YAML 卡片定义。"""
        return OpenAIChatModel.list_models(
            custom_yaml_dir=str(custom_yaml_dir or PLAN_MODELS_DIR),
        )


class ZhipuCodingPlanCredential(OpenAICredential):
    """智谱 Coding Plan 凭证（OpenAI 兼容端点）。"""

    model_config = ConfigDict(title="智谱 Coding Plan")

    type: Literal["zhipu_coding_plan"] = "zhipu_coding_plan"
    """凭证类型判别字段。"""

    @classmethod
    def get_chat_model_class(cls) -> Type["ZhipuPlanChatModel"]:
        """把本凭证绑定到限套餐模型的对话模型类。"""
        return ZhipuPlanChatModel

    @classmethod
    def get_embedding_model_class(cls) -> Type["ZhipuPlanEmbeddingModel"]:
        """Bind an empty embedding catalog.

        The Coding Plan endpoint serves chat completions only — without
        this override the credential inherits OpenAI's embedding cards
        (text-embedding-3-*) and the knowledge-base picker offers
        models the endpoint would reject at indexing time.
        """
        return ZhipuPlanEmbeddingModel


class ZhipuPlanEmbeddingModel(OpenAIEmbeddingModel):
    """Embedding model with an empty catalog (plan has no embedding API)."""

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list:
        """Surface nothing — the plan endpoint has no embedding models."""
        return []


class BgeEmbeddingModel(OpenAIEmbeddingModel):
    """Embedding model backed by the local TEI container (BGE weights).

    The catalog is the local ``models/bge/*.yaml`` cards. TEI's
    OpenAI-compatible route ignores/rejects the ``dimensions`` request
    parameter for non-Matryoshka models such as bge-small-zh-v1.5, so
    ``pass_dimensions`` defaults to ``False`` here.

    ``_TEXT_BATCH_SIZE`` must stay at TEI's ``--max-client-batch-size``
    validation limit (default 32): the framework's default of 2048
    makes large documents fail with 413 "batch size N > maximum
    allowed batch size 32" at indexing time.

    The framework treats ``context_size`` as metadata only — it never
    truncates inputs, while TEI hard-rejects anything at/over the
    model's context (bge-small-zh-v1.5: < 512 tokens). Real token
    counts cannot be estimated linearly from character classes
    (measured 92-1268 tokens across same-size chunks of a GB-standard
    PDF), so ``_call_api`` asks TEI's ``/tokenize`` for exact counts
    and proportionally trims over-limit inputs (re-check, ≤ 3 rounds).
    A conservative character clip is the fallback if /tokenize is
    unreachable.
    """

    _TEXT_BATCH_SIZE: int = 32

    def __init__(
        self,
        *args: Any,
        pass_dimensions: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, pass_dimensions=pass_dimensions, **kwargs)

    @staticmethod
    def _clip_to_context(text: str, limit: int) -> str:
        """Fallback: clip to a conservative sub-``limit`` token estimate."""
        estimate = 0.0
        for i, ch in enumerate(text):
            estimate += 1.5 if ord(ch) > 0x2E80 else 0.35
            if estimate > limit:
                return text[: max(0, i - 16)]
        return text

    def _tei_root(self) -> str:
        """TEI server root, derived from the OpenAI-compatible base_url."""
        base = str(getattr(self.client, "base_url", "")).rstrip("/")
        return base[: -3] if base.endswith("/v1") else base

    async def _real_token_counts(self, texts: list[str]) -> list[int]:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._tei_root()}/tokenize",
                json={"inputs": texts},
            )
            resp.raise_for_status()
            return [len(enc) for enc in resp.json()]

    async def _fit_to_context(self, inputs: list[str]) -> list[str]:
        """Return ``inputs`` with every entry strictly under the context."""
        limit = max(0, int(self.context_size) - 2)
        final = list(inputs)
        live = [i for i, t in enumerate(final) if t]
        if not live:
            return final
        try:
            counts = await self._real_token_counts(
                [final[i] for i in live]
            )
        except Exception:  # noqa: BLE001 — fall back to the char clip
            return [
                self._clip_to_context(t, limit) if t else t for t in final
            ]
        for _ in range(3):
            over = [
                (idx, cnt)
                for idx, cnt in zip(live, counts)
                if cnt > limit
            ]
            if not over:
                break
            probe = []
            for idx, cnt in over:
                new_len = max(32, int(len(final[idx]) * limit * 0.97 / cnt))
                final[idx] = final[idx][:new_len]
                probe.append(final[idx])
            fresh = await self._real_token_counts(probe)
            refreshed = dict(zip((idx for idx, _ in over), fresh))
            counts = [
                refreshed.get(idx, cnt)
                for idx, cnt in zip(live, counts)
            ]
        return final

    async def _call_api(
        self,
        inputs: list[str],
        **kwargs: Any,
    ):
        clipped = await self._fit_to_context(inputs)
        return await super()._call_api(clipped, **kwargs)

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ):
        """Only surface the locally hosted embedding models."""
        return OpenAIEmbeddingModel.list_models(
            custom_yaml_dir=str(custom_yaml_dir or BGE_MODELS_DIR),
        )


class LocalBgeCredential(OpenAICredential):
    """Credential for the local TEI embedding endpoint (no real auth).

    The TEI container accepts any API key; scripts write the placeholder
    ``local``. ``base_url`` must point at the TEI OpenAI-compatible
    route (default http://localhost:19556/v1).
    """

    model_config = ConfigDict(title="本地 BGE (TEI)")

    type: Literal["local_bge"] = "local_bge"
    """The credential type discriminator."""

    @classmethod
    def get_embedding_model_class(cls) -> Type["BgeEmbeddingModel"]:
        """Bind this credential to the local-BGE embedding model class."""
        return BgeEmbeddingModel
