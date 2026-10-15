"""公共配置: 从 .env 读取 Z.ai 配置并构建模型实例。"""
import os
from pathlib import Path

from dotenv import load_dotenv
from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

ZAI_API_KEY = os.environ["ZAI_API_KEY"]
ZAI_BASE_URL = os.environ["ZAI_BASE_URL"]
ZAI_MODEL = os.environ["ZAI_MODEL"]


def build_model(**kwargs) -> OpenAIChatModel:
    """构建连接 Z.ai (OpenAI 兼容) 的模型实例。"""
    defaults = dict(
        credential=OpenAICredential(
            api_key=ZAI_API_KEY,
            base_url=ZAI_BASE_URL,
        ),
        model=ZAI_MODEL,
        stream=True,
    )
    defaults.update(kwargs)
    return OpenAIChatModel(**defaults)
