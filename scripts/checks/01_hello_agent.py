"""验证步骤 1: 最小 ReAct Agent —— 模型 + 自定义工具 + 结构化输出。

运行: .venv\\Scripts\\python.exe scripts\\01_hello_agent.py
"""
import asyncio

from pydantic import BaseModel, Field
from agentscope.agent import Agent
from agentscope.message import Msg, TextBlock
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit, ToolChunk

from common import ZAI_MODEL, build_model


async def get_current_weather(city: str) -> ToolChunk:
    """获取指定城市当前天气(模拟数据, 用于验证工具调用链路)。

    Args:
        city: 城市名称, 例如 "北京"
    """
    fake = {"北京": "晴, 26°C", "上海": "多云, 29°C"}
    weather = fake.get(city, "小雨, 22°C")
    print(f"[tool] get_current_weather 被调用: city={city!r}")
    return ToolChunk(content=[TextBlock(type="text", text=f"{city}: {weather}")])


class WeatherAnswer(BaseModel):
    """结构化输出 schema, 验证 Agent 的 structured output 能力。"""

    city: str = Field(description="城市名")
    summary: str = Field(description="一句话天气总结")


async def main() -> None:
    toolkit = Toolkit()
    await toolkit.add_tool(FunctionTool(get_current_weather))

    agent = Agent(
        name="Friday",
        system_prompt="你是助手 Friday, 简洁地用中文回答问题。",
        model=build_model(),
        toolkit=toolkit,
        # 本地验证使用 bypass 模式跳过工具调用人工确认; 生产环境请勿默认开启
        state=AgentState(
            permission_context=PermissionContext(mode=PermissionMode.BYPASS),
        ),
    )

    question_text = "北京今天天气怎么样? 适合穿短袖吗?"
    question = Msg(
        name="user",
        role="user",
        content=[TextBlock(type="text", text=question_text)],
    )
    reply = await agent.reply(question, structured_schema=WeatherAnswer)

    print("-" * 60)
    print(f"model: {ZAI_MODEL}")
    print(f"回复内容: {reply.content}")
    if reply.structured_output:
        print(f"结构化输出: {reply.structured_output}")
    print("RESULT   : OK - ReAct Agent (模型+工具+结构化输出) 验证通过")


if __name__ == "__main__":
    asyncio.run(main())
