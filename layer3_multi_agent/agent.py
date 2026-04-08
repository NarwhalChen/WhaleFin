"""
Layer 3: 多 Agent 体系

在 Layer 2 基础上新增:
- run_loop 加两个参数:
    system: str  → 每个 agent 有自己的角色 prompt
    interactive: bool → False 时 end_turn 直接返回结果，不等用户输入
- AgentTool：把子 agent 包装成普通工具，走同一套 tool_execution pipeline
- AGENT_CONFIGS：name → {system_prompt, tools}，定义可用的子 agent

import-forward: 复用 Layer 2 的工具和 pipeline，run_loop 在此扩展
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 加载 .env
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()

from anthropic import AsyncAnthropic
from layer2_tool_system.tools import ALL_TOOLS
from layer2_tool_system.tool_execution import run_tools
from layer3_multi_agent.agents.agent_tool import AgentTool

DEFAULT_SYSTEM_PROMPT = "You are a helpful coding assistant."
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096


async def run_loop(
    messages: list,
    tools: list = [],
    client: AsyncAnthropic = None,
    system: str = DEFAULT_SYSTEM_PROMPT,   # Layer 3 新增：可传入不同角色 prompt
    interactive: bool = True,               # Layer 3 新增：False = 子 agent 模式
) -> str | None:
    """
    Layer 3 主循环。

    interactive=True  (主 agent): end_turn → 等用户输入，永远跑下去
    interactive=False (子 agent): end_turn → 返回最后一条回复，结束循环
    """
    api_tools = [t.to_api_format() for t in tools]

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

        if final.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": final.content})

            tool_use_blocks = [b for b in final.content if b.type == "tool_use"]
            if interactive:
                print(f"\n[Tools] 执行 {len(tool_use_blocks)} 个工具调用...")

            ordered_results = await run_tools(tool_use_blocks, tools)

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
                # 子 agent 模式：返回结果给 AgentTool.call()
                return full_response

            # 主 agent 模式：等用户输入
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

    # AgentTool 注入 client 和 messages 引用
    # main_messages_ref 指向同一个 list，call() 时 deepcopy 当前状态
    agent_tool = AgentTool(client=client, main_messages_ref=messages)
    tools = ALL_TOOLS + [agent_tool]

    print(f"[Layer 3] 工具已加载: {[t.name for t in tools]}")
    print(f"[Layer 3] 可用 agent: {list(__import__('layer3_multi_agent.agents.configs', fromlist=['AGENT_CONFIGS']).AGENT_CONFIGS.keys())}")

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
