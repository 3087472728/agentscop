# -*- coding: utf-8 -*-
# ============================================================
# 模块名: zhipu_credential.py
# 用途:   注册「智谱 GLM」原生凭据类型。
#         框架内置凭据只有 OpenAI / DeepSeek / Moonshot 等，没有智谱。
#         此前用户只能在「OpenAI API」类型下手填 base_url 才能接智谱，
#         留空则被当成 OpenAI 官方端点，导致密钥无效、模型列表为空。
#         本模块定义 ZhipuCredential（继承 OpenAICredential，完整复用
#         OpenAI 兼容调用链），base_url 默认指向智谱端点，用户在
#         前端「添加凭据」时选择「智谱 GLM API」只需填密钥即可。
# 用法:   setup() 必须在 app_factory 组装应用之前调用（见 main.py），
#         通过官方扩展点 CredentialFactory.register_credential 注册。
# ============================================================
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field

from agentscope.credential import CredentialFactory, OpenAICredential

if TYPE_CHECKING:
    from agentscope.model import ChatModelBase

# 智谱 OpenAI 兼容端点（/chat/completions、/models、/embeddings 等）
_ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


class ZhipuCredential(OpenAICredential):
    """智谱 GLM 凭据：OpenAI 兼容协议，端点固定为智谱官方地址。"""

    model_config = ConfigDict(
        title="智谱 GLM API",
    )

    type: Literal["zhipu_credential"] = "zhipu_credential"
    """The credential type."""

    base_url: str = Field(
        default=_ZHIPU_BASE_URL,
        description="智谱 OpenAI 兼容端点，一般无需修改",
    )
    """智谱 OpenAI 兼容端点。"""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """复用 OpenAIChatModel：构造时直接读取 credential.base_url。"""
        from agentscope.model import OpenAIChatModel

        return OpenAIChatModel


def setup() -> None:
    """把 ZhipuCredential 注册进 CredentialFactory（幂等）。

    注册后：
      - /credential/schemas 会返回该类型，前端「添加凭据」下拉出现
        「智谱 GLM API」选项，表单只需填 api_key；
      - 运行时 CredentialFactory.from_dict 能反序列化该类型凭据；
      - /model/?provider=zhipu_credential 走 OpenAIChatModel.list_models()
        （被 server/remote_models.py 打过补丁，含动态发现的 GLM 模型）。
    """
    CredentialFactory.register_credential(ZhipuCredential)
