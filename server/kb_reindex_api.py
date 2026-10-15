# -*- coding: utf-8 -*-
# ============================================================
# 模块名: kb_reindex_api.py
# 用途:   文档重新索引（重传）接口——
#         对索引失败（status=error）或卡住的文档，用已保存的 blob
#         重新走标准上传流程，触发框架重新解析/切分/向量化。
#
#         框架自带上传接口 POST /knowledge_bases/{kb}/documents，
#         本模块新增 POST /knowledge_bases/{kb}/documents/{doc}/reindex，
#         从原 blob 读文件流重新 register_document，成功后删除旧记录。
# 接口:   setup(app) —— 向 knowledge_base_router 追加路由
# ============================================================
import logging
import os

from fastapi import Depends, HTTPException

from agentscope.app.deps import (
    get_current_user_id,
    get_knowledge_base_service,
)

logger = logging.getLogger("main.kb_reindex")

from . import paths


async def _reindex_document(
    knowledge_base_id: str,
    document_id: str,
    user_id: str = Depends(get_current_user_id),
    service=Depends(get_knowledge_base_service),
) -> dict:
    """用已有 blob 重新索引指定文档。

    流程：读旧文档记录 → 定位 blob → 重新 register_document（标准
    索引流程）→ 删除旧记录。用于失败/卡住文档的一键重传。
    """
    # 1) 读旧文档（service 内部做权限校验）
    try:
        record = await service.get_document(user_id, knowledge_base_id, document_id)
    except Exception as exc:  # noqa: BLE001 — 不存在或无权限
        raise HTTPException(status_code=404, detail=f"文档不存在: {exc}") from exc

    data = record.data
    blob_uri = str(getattr(data, "blob_uri", "") or "")
    if not blob_uri.startswith("local://"):
        raise HTTPException(status_code=422, detail="不支持的 blob 存储类型")
    blob_id = blob_uri.rsplit("/", 1)[-1]
    blob_path = os.path.join(paths.BLOBS_DIR, "kb", knowledge_base_id, blob_id)
    if not os.path.exists(blob_path):
        raise HTTPException(status_code=410, detail="原始文件已丢失，无法重传")

    # 2) 用 blob 内容重新注册（触发标准索引流程）
    with open(blob_path, "rb") as stream:
        new_record = await service.register_document(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            filename=data.filename,
            stream=stream,
            size=int(getattr(data, "size", 0) or 0),
            content_type=getattr(data, "content_type", None),
        )

    # 3) 删除旧记录（含旧 blob）；放在最后，注册失败则旧记录仍在
    try:
        await service.delete_document(user_id, knowledge_base_id, document_id)
    except Exception as exc:  # noqa: BLE001 — 旧记录删除失败不影响新记录
        logger.warning("删除旧文档记录失败(%s): %s", document_id, exc)

    logger.info(
        "文档重传: %s/%s -> 新文档 %s", knowledge_base_id, document_id, new_record.id,
    )
    return {
        "document_id": new_record.id,
        "filename": new_record.data.filename,
        "status": new_record.status,
    }


def setup(app) -> None:
    """把 reindex 路由追加到框架的 knowledge_base_router。"""
    from fastapi.routing import APIRoute, _IncludedRouter

    target = "/knowledge_bases/embedding_models"  # 用于识别 knowledge_base_router
    for wrapper in app.router.routes:
        if not isinstance(wrapper, _IncludedRouter):
            continue
        router = wrapper.original_router
        paths = {getattr(r, "path", "") for r in router.routes}
        if target not in paths:
            continue
        router.routes.append(
            APIRoute(
                path="/knowledge_bases/{knowledge_base_id}/documents/{document_id}/reindex",
                endpoint=_reindex_document,
                methods=["POST"],
                name="reindex_knowledge_document",
                tags=["knowledge_bases"],
                summary="Re-index an existing document from its stored blob",
            ),
        )
        logger.info("已注册文档重传接口 /knowledge_bases/{kb}/documents/{doc}/reindex")
        return
    logger.warning("未找到 knowledge_base_router，跳过 reindex 路由注册")
