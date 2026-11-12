# -*- coding: utf-8 -*-
# ============================================================
# 模块名: download_token_ttl_patch.py
# 用途:   放宽工作区文件下载令牌的有效期。
#
#         框架默认 DEFAULT_DOWNLOAD_TOKEN_TTL = 60 秒。令牌随
#         下载链接一起写在聊天消息里，用户往往不会在 60 秒内点击，
#         链接点开就是 "Expired download token"；且项目把签名密钥
#         持久化到 config/download_secret.key 的初衷就是
#         "重启后历史下载链接不失效"，60 秒 TTL 与该意图矛盾。
#
#         本补丁包装 WorkspaceService.sign_download_token，
#         注入可配置的 TTL（环境变量 AGENTSCOPE_DOWNLOAD_TOKEN_TTL，
#         单位秒，默认 3600）。签名内容不变（expires_at/user/path），
#         旧链接验证逻辑不受影响。
#
#         迁移自服务器 /home/agentrunner/agentscop 未提交修改
#         （src/agentscop/patches/download_token_ttl_patch.py，
#         2026-11-12）；本地无 patches 聚合层，由 server/app.py
#         直接调用 setup()。
# 接口:   setup() —— 幂等安装
# ============================================================
import logging
import os

from agentscope.app._service._workspace import WorkspaceService

logger = logging.getLogger("uvicorn.error")

# 令牌有效期（秒）：环境变量可调，默认 1 小时
DOWNLOAD_TOKEN_TTL = int(
    os.environ.get("AGENTSCOPE_DOWNLOAD_TOKEN_TTL", "3600")
)

_sign_orig = WorkspaceService.sign_download_token


def _sign_download_token(self, user_id, path, ttl=DOWNLOAD_TOKEN_TTL):
    """与原方法签名一致，仅替换默认 TTL。"""
    return _sign_orig(self, user_id, path, ttl=ttl)


def setup() -> None:
    """包装 sign_download_token，幂等。"""
    if getattr(WorkspaceService.sign_download_token, "__dl_ttl_patch__", False):
        return
    WorkspaceService.sign_download_token = _sign_download_token  # type: ignore[method-assign]
    _sign_download_token.__dl_ttl_patch__ = True  # type: ignore[attr-defined]
    logger.info(
        "已启用下载令牌有效期补丁（TTL=%ss，环境变量 "
        "AGENTSCOPE_DOWNLOAD_TOKEN_TTL 可调）",
        DOWNLOAD_TOKEN_TTL,
    )
