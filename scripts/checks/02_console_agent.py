"""验证步骤 2: 交互式终端 Agent(用于人工对话验证)。

运行: .venv\\Scripts\\python.exe scripts\\02_console_agent.py
输入 exit 或 Ctrl+C 退出。
"""
import asyncio

from agentscope.agent import Agent
from agentscope.console import launch_console
from agentscope.tool import Toolkit

from common import build_model


async def main() -> None:
    toolkit = Toolkit()

    agent = Agent(
        name="Friday",
        system_prompt="你是助手 Friday, 在终端中与用户对话, 用中文简洁回答。",
        model=build_model(),
        toolkit=toolkit,
    )

    await launch_console(agent)


if __name__ == "__main__":
    asyncio.run(main())
