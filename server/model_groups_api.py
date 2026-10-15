# -*- coding: utf-8 -*-
# ============================================================
# 模块名: model_groups_api.py
# 用途:   按凭据返回对话模型清单，供前端「每个凭据只显示自己的模型」。
#
#         框架 /model/?provider=xxx 返回的是 provider 类型的全局清单，
#         前端 useAvailableModels 把同一份全局清单分配给同类型的每个凭据，
#         导致 embed-local-8003 也显示 GLM/GPT 等对话模型。
#         本接口返回 {credential_id: [模型名]}，前端按可见凭据匹配即可。
# 接口:   GET /model_groups/  ->  {"models_by_credential": {...}}
# 接口:   setup(app) —— 注册路由，启动时调用一次
# 数据:   复用 remote_models 后台任务按凭据分组的结果
# ============================================================
import logging

from fastapi import Header

from . import remote_models

logger = logging.getLogger("main.model_groups")


def setup(app) -> None:
    """注册 /model_groups/ 路由。"""

    @app.get("/model_groups/")
    async def list_model_groups(
        x_user_id: str | None = Header(default=None),
    ) -> dict:
        """按凭据返回动态发现的对话模型名。

        前端用 /credential/ 拿到自己可见的凭据，再按 credential_id
        在此结果里取各自的模型；无对应条目的凭据 models 为空，
        由前端过滤（如 embedding 专用凭据不会出现在对话模型下拉里）。
        """
        by_cred = remote_models.get_models_by_credential()
        return {
            "models_by_credential": {
                # 返回完整模型卡（含 input_types 等），前端直接使用，
                # 保证「附件按钮按模型能力启用/禁用」等逻辑拿到真实数据。
                cid: [card.model_dump(mode="json") for card in cards]
                for cid, cards in by_cred.items()
            }
        }
