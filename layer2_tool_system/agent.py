"""
Layer 2: 工具系统

在 Layer 1 基础上新增:
- 工具定义传给 Claude API (tools 参数)
- stop_reason 判断: tool_use → 执行工具; end_turn → 等用户输入
- tool_use 之后 messages 存两条 (必须，否则 Claude 下轮失忆):
    第一条: {"role": "assistant", "content": final.content}  ← 原样存 content list
    第二条: {"role": "user", "content": [tool_result blocks]}

import-forward: 复用 Layer 1 的常量，run_loop 在此重新实现并扩展
"""

import asyncio
import sys
import os
from pathlib import Path

# 从项目根目录的 .env 加载 API key
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from anthropic import AsyncAnthropic
from layer1_main_loop.agent import MODEL, MAX_TOKENS, SYSTEM_PROMPT
from layer2_tool_system.tools import ALL_TOOLS
from layer2_tool_system.tool_execution import run_tools


async def run_loop(
    messages: list,
    tools: list = [],
    client: AsyncAnthropic = None,
) -> None:
    """
    Layer 2 主循环。
    和 Layer 1 的区别: 处理 stop_reason == "tool_use" 的情况。
    """
    # 把工具转换为 API 格式
    api_tools = [t.to_api_format() for t in tools]

    while True:
        full_response = ""
        print("\nAssistant: ", end="", flush=True)

        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=api_tools if api_tools else [],
        ) as stream:
            async for text in stream.text_stream:
                print(text, end="", flush=True)
                full_response += text

            final = await stream.get_final_message()

        print()

        # stop_reason 决定下一步
        if final.stop_reason == "tool_use":
            # 第一条: 原样存 assistant 的 content list (包含 tool_use blocks)
            messages.append({"role": "assistant", "content": final.content})

            # 找出所有 tool_use blocks 并执行
            tool_use_blocks = [b for b in final.content if b.type == "tool_use"]
            print(f"\n[Tools] 执行 {len(tool_use_blocks)} 个工具调用...")

            ordered_results = await run_tools(tool_use_blocks, tools)

            # 第二条: 按顺序存所有 tool_result
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result,
                }
                for tool_id, result in ordered_results
            ]
            messages.append({"role": "user", "content": tool_results})

            # 不等用户输入，直接进下一轮让 Claude 处理结果
            continue

        else:
            # end_turn: 正常回复，等用户输入
            if full_response:
                messages.append({"role": "assistant", "content": full_response})

            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[退出]")
                break

            if not user_input:
                continue

            messages.append({"role": "user", "content": user_input})


async def main() -> None:
    client = AsyncAnthropic()
    messages: list = []
    tools = ALL_TOOLS

    print(f"[Layer 2] 工具已加载: {[t.name for t in tools]}")

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
