# -*- coding: utf-8 -*-
"""聊天图片上传端点 + 模型调用时的相对 URL 解析补丁。

端点：

    POST /chat_uploads/      上传一张图片（multipart，Basic 认证）
    GET  /chat_uploads/{fn}  取回图片（无鉴权；<img> 标签无法携带
                             请求头，与 figures_router 同一信任边界）

图片落盘 ``data/blobs/chat/<uuid><ext>``，消息里存 blob URI
``local://chat/<file>``（DataBlock + URLSource）——URLSource.url 是
pydantic AnyUrl、不接受相对路径，``local://`` 是 LocalBlobStore 的
原生 URI 形式且与部署主机无关；前端渲染时映射为 HTTP URL，避免
base64 原文撑大 messages.payload 与 SSE 事件流。

补丁：框架 OpenAI 兼容 formatter 对 URLSource 直接透传给模型 API，
模型侧无法回取本机 URI；这里包装
``_OpenAIFormatterBase._format_image_source``，把 ``local://chat/``
前缀的 URI 就地读盘转成 base64 data URI 再发给模型。仅放行本目录
内的文件，不构成任意文件读取。
"""
import base64
import logging
import mimetypes
import os
import uuid
from pathlib import Path

from agentscope.app.deps import get_current_user_id
from agentscope.message import URLSource
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import paths

logger = logging.getLogger(__name__)

# 与 server/remote_models.py 的 _VISION_INPUT_TYPES 保持一致，另允许 gif
_ALLOWED_MEDIA_TYPES = frozenset(
    ("image/png", "image/jpeg", "image/webp", "image/gif"),
)
# 单图上限 20 MB：足够截图/照片，超出应走文件工作区
_MAX_UPLOAD_SIZE = 20 * 1024 * 1024

CHAT_UPLOADS_DIR = os.path.join(paths.BLOBS_DIR, "chat")
os.makedirs(CHAT_UPLOADS_DIR, exist_ok=True)

# 消息里存储的 blob URI 前缀（与 LocalBlobStore 的 local://{key} 一致，
# key = chat/<filename>）
_URI_PREFIX = "local://chat/"

chat_uploads_router = APIRouter(prefix="/chat_uploads", tags=["chat_uploads"])


def _resolve_upload_path(filename: str) -> Path:
    """把 URL 中的文件名解析为目录内的绝对路径（防目录穿越）。"""
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
    ):
        raise HTTPException(status_code=404)
    path = (Path(CHAT_UPLOADS_DIR) / filename).resolve()
    try:
        path.relative_to(Path(CHAT_UPLOADS_DIR).resolve())
    except ValueError:
        raise HTTPException(status_code=404)  # noqa: B904 — plain 404
    return path


@chat_uploads_router.post("/")
async def upload_chat_image(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """接收一张图片，落盘后返回可内联引用的相对 URL。"""
    del user_id  # 仅用于鉴权
    media_type = (file.content_type or "").split(";")[0].strip().lower()
    if media_type not in _ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported type: {media_type}")
    data = await file.read()
    if len(data) > _MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Image exceeds 20 MB limit")

    ext = mimetypes.guess_extension(media_type) or ".bin"
    # guess_extension 对 image/jpeg 可能返回 .jpe（个别平台），归一为 .jpg
    if ext in (".jpe", ".bin"):
        ext = ".jpg" if media_type == "image/jpeg" else ".png"
    filename = uuid.uuid4().hex + ext
    with open(os.path.join(CHAT_UPLOADS_DIR, filename), "wb") as out:
        out.write(data)
    return {
        # blob URI（消息里存的引用）；HTTP 回显走 GET /chat_uploads/{fn}
        "url": f"local://chat/{filename}",
        "media_type": media_type,
        "name": file.filename or filename,
    }


@chat_uploads_router.get("/{filename}")
async def get_chat_image(filename: str) -> FileResponse:
    """服务一张已上传的聊天图片。"""
    path = _resolve_upload_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


def _patch_openai_formatter() -> None:
    """让 OpenAI 兼容 formatter 能解析 /chat_uploads/ 相对 URL。"""
    from agentscope.formatter._openai_formatter import _OpenAIFormatterBase

    original = _OpenAIFormatterBase._format_image_source
    if getattr(original, "_chat_uploads_patched", False):
        return  # 幂等：重复 setup 不叠加包装

    def patched(self, source):  # noqa: ANN001, ANN202 — 保持原签名
        if isinstance(source, URLSource):
            url_str = str(source.url)
            if url_str.startswith(_URI_PREFIX):
                try:
                    path = _resolve_upload_path(url_str[len(_URI_PREFIX):])
                except HTTPException:
                    logger.warning("Malformed chat image URI skipped: %r", url_str)
                    return None
                if path.is_file():
                    with open(path, "rb") as f:
                        encoded = base64.b64encode(f.read()).decode("utf-8")
                    return {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{source.media_type};base64,{encoded}",
                        },
                    }
                # 文件已被清理：跳过该块比中断整次对话更友好
                logger.warning("Chat image missing on disk, skipped: %r", url_str)
                return None
        return original(self, source)

    patched._chat_uploads_patched = True  # type: ignore[attr-defined]
    _OpenAIFormatterBase._format_image_source = patched  # type: ignore[method-assign]


def setup(app) -> None:  # noqa: ANN001 — FastAPI 实例，避免循环导入
    """挂载路由并打 formatter 补丁（由 server/app.py 调用）。"""
    app.include_router(chat_uploads_router)
    _patch_openai_formatter()
