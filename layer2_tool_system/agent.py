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
from layer1_main_loop.agent import MODEL, MAX_TOKENS, _build_system_prompt, StopReason
from layer2_tool_system.tools import ALL_TOOLS
from layer2_tool_system.tool_execution import run_tools, StreamingToolExecutor


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

    state = {
        "continue_reason": None,
        "full_response": "",
        "last_usage": None,
    }

    while True:
        state["full_response"] = ""
        print("\nAssistant: ", end="", flush=True)

        executor = StreamingToolExecutor(tools)

        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_build_system_prompt(),
            messages=messages,
            tools=api_tools if api_tools else [],
        ) as stream:
            async for event in stream:
                text = executor.on_event(event)
                if text:
                    print(text, end="", flush=True)
                    state["full_response"] += text

            final = await stream.get_final_message()
            state["last_usage"] = final.usage

        print()

        # ── continue 点: TOOL_USE ────────────────────────────────────────────
        if final.stop_reason == StopReason.TOOL_USE:
            tool_count = len(executor._tool_order)
            print(f"\n[Tools] 执行 {tool_count} 个工具调用（streaming 模式）...")

            ordered_results = await executor.finish()

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
            state["continue_reason"] = StopReason.TOOL_USE
            continue

        # ── continue 点: MAX_TOKENS ──────────────────────────────────────────
        if final.stop_reason == StopReason.MAX_TOKENS:
            if state["full_response"]:
                messages.append({"role": "assistant", "content": state["full_response"]})
            messages.append({"role": "user", "content": "<continue_interrupted_response/>"})
            state["continue_reason"] = StopReason.MAX_TOKENS
            continue

        # ── continue 点: END_TURN ────────────────────────────────────────────
        if state["full_response"]:
            messages.append({"role": "assistant", "content": state["full_response"]})

        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[退出]")
            break

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        state["continue_reason"] = StopReason.END_TURN


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
