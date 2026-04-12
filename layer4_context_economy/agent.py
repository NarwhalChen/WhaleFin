"""
Layer 4: Context Economy

在 Layer 3 基础上新增:
- CompactTool: 压缩对话历史，AI 主动调用 + 系统自动触发
- token 监控: 每轮从 API response usage 读取真实 token 数
- 自动触发: token/MAX_TOKENS 超过阈值时，在处理 response 前先压缩
- system prompt 注入压缩规则，约束 Claude 单独调用 compact

设计决策:
- 阈值触发 (proactive) 优先于 AI 判断触发，避免 token 超限报错
- 用 response.usage.input_tokens 而非估算，精确
- compact 本身不进 tool_use pipeline，直接调 CompactTool.call()，不需要 Claude 参与
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()

from anthropic import AsyncAnthropic
from layer2_tool_system.tools import ALL_TOOLS
from layer2_tool_system.tool_execution import run_tools
from layer3_multi_agent.agents.agent_tool import AgentTool
from layer4_context_economy.tools.compact_tool import CompactTool

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096
COMPACT_THRESHOLD = 0.8  # token 用量超过 80% 触发自动压缩

DEFAULT_SYSTEM_PROMPT = """\
You are a helpful coding assistant.

## Context Economy Rules
当 token 用量超过上限的 80% 时，调用 compact 工具压缩历史。
compact 工具必须单独调用，不能与其他工具并行。
看到 <summary> 标签时，这是之前对话的压缩摘要，不是用户消息，直接当作历史上下文使用。
"""


async def run_loop(
    messages: list,
    tools: list = [],
    client: AsyncAnthropic = None,
    system: str = DEFAULT_SYSTEM_PROMPT,
    interactive: bool = True,
) -> str | None:
    api_tools = [t.to_api_format() for t in tools]

    # 找到 compact tool 引用，用于自动触发
    compact_tool = next((t for t in tools if t.name == "compact"), None)

    while True:
        full_response = ""
        if interactive:
            print("\nAssistant: ", end="", flush=True)

        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            tools=api_tools if api_tools else [],
        ) as stream:
            async for text in stream.text_stream:
                if interactive:
                    print(text, end="", flush=True)
                full_response += text

            final = await stream.get_final_message()

        if interactive:
            print()

        # token 监控：API response 返回后，处理 response 前
        # 用真实 input_tokens，比估算精确
        if compact_tool and final.usage.input_tokens / MAX_TOKENS > COMPACT_THRESHOLD:
            print(f"\n[Auto-compact] token 用量 {final.usage.input_tokens}/{MAX_TOKENS} ({final.usage.input_tokens/MAX_TOKENS:.0%})，自动压缩...")
            result = await compact_tool.call({})
            print(f"[Auto-compact] {result}")

        if final.stop_reason == "tool_use":
            tool_use_blocks = [b for b in final.content if b.type == "tool_use"]
            if interactive:
                print(f"\n[Tools] 执行 {len(tool_use_blocks)} 个工具调用...")

            ordered_results = await run_tools(tool_use_blocks, tools)

            messages.append({"role": "assistant", "content": final.content})
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result,
                }
                for tool_id, result in ordered_results
            ]
            messages.append({"role": "user", "content": tool_results})
            continue

        else:  # end_turn
            if full_response:
                messages.append({"role": "assistant", "content": full_response})

            if not interactive:
                return full_response

            if interactive:
                print("\nAssistant: ", end="") if not full_response else None
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

    compact_tool = CompactTool(client=client, main_messages_ref=messages)
    agent_tool = AgentTool(client=client, main_messages_ref=messages)
    tools = ALL_TOOLS + [agent_tool, compact_tool]

    print(f"[Layer 4] 工具已加载: {[t.name for t in tools]}")
    print(f"[Layer 4] 自动压缩阈值: {COMPACT_THRESHOLD:.0%}")

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
