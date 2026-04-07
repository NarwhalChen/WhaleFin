"""
Layer 1: 主循环状态机

核心问题: 为什么是 while True，不是递归？
- 递归: 状态藏在调用栈里，长对话会爆栈 (Python 默认 recursion limit = 1000)
- while True: 状态显式存在 messages list 里，可以随时打印、检查、修改

为什么从 Layer 1 就用 AsyncAnthropic？
- Layer 3 的 background agent 需要 asyncio.create_task
- create_task 只能在 event loop 里调用
- 同步主循环没有 event loop，到 Layer 3 就必须推倒重来
- 现在多付 async/await 的一次性成本，换来 Layer 3 不需要重构
"""

import asyncio
import os
from pathlib import Path
from anthropic import AsyncAnthropic

# 从项目根目录的 .env 加载 API key
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()

SYSTEM_PROMPT = "You are a helpful coding assistant."
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096


async def run_loop(
    messages: list,
    tools: list = [],        # Layer 1 无工具，默认空列表；Layer 2+ 传入实际工具
    client: AsyncAnthropic = None,
) -> None:
    """
    主循环。每轮:
    1. 把当前 messages 发给 Claude，streaming 接收回复
    2. 边收边打印文本片段 (content_block_delta)
    3. 流结束后把完整回复存入 messages (role: assistant)
    4. 等待用户输入，存入 messages (role: user)，进入下一轮
    """
    while True:
        # 1. 调用 Claude streaming API
        full_response = ""
        print("\nAssistant: ", end="", flush=True)

        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            # 边收边打印，产生 streaming 效果
            async for text in stream.text_stream:
                print(text, end="", flush=True)
                full_response += text

        print()  # 换行

        # 2. 把完整回复存入 messages
        messages.append({"role": "assistant", "content": full_response})

        # 3. 等待用户输入
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[退出]")
            break

        if not user_input:
            continue

        # 4. 存入 messages，进入下一轮
        messages.append({"role": "user", "content": user_input})


async def main() -> None:
    # 初始化 client (AsyncAnthropic 不是 Anthropic，否则会默默阻塞事件循环)
    client = AsyncAnthropic()

    # 初始化 messages，第一条是用户发起对话
    messages: list = []

    # 初始化 tools (Layer 1 无工具)
    tools: list = []

    # 让用户先说第一句话
    try:
        first_input = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    if not first_input:
        return

    messages.append({"role": "user", "content": first_input})

    await run_loop(messages, tools, client)


if __name__ == "__main__":
    asyncio.run(main())
