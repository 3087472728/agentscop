"""验证步骤 0: 直接调用模型, 确认 API Key / 端点 / 模型名可用。"""
import asyncio

from agentscope.message import Msg, TextBlock

from common import ZAI_BASE_URL, ZAI_MODEL, build_model


async def main() -> None:
    model = build_model(stream=False)
    print(f"endpoint : {ZAI_BASE_URL}")
    print(f"model    : {ZAI_MODEL}")

    response = await model(
        messages=[
            Msg(
                name="user",
                role="user",
                content=[TextBlock(type="text", text="用一句话介绍 AgentScope。")],
            ),
        ],
    )
    first = response[0] if isinstance(response, list) else response
    print(f"reply    : {first.content}")
    print("RESULT   : OK - 模型连通性验证通过")


if __name__ == "__main__":
    asyncio.run(main())
