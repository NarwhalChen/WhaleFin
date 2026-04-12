"""
Layer 3: 多 Agent 体系

在 Layer 2 基础上新增:
- run_loop 加两个参数:
    system: str  → 每个 agent 有自己的角色 prompt
    interactive: bool → False 时 end_turn 直接返回结果，不等用户输入
- AgentTool：把子 agent 包装成普通工具，走同一套 tool_execution pipeline
- AGENT_CONFIGS：name → {system_prompt, tools}，定义可用的子 agent

import-forward: 复用 Layer 1/2 的 pipeline，run_loop 在此扩展
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
from layer1_main_loop.agent import _build_system_prompt, StopReason, AGENT_BOUNDARY
from layer2_tool_system.tools import ALL_TOOLS
from layer2_tool_system.tool_execution import StreamingToolExecutor
from layer2_tool_system.hooks import HookRegistry, DEFAULT_REGISTRY
from layer3_multi_agent.agents.agent_tool import AgentTool

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096


async def run_loop(
    messages: list,
    tools: list = [],
    client: AsyncAnthropic = None,
    system: str = None,                      # None → 使用 Layer 1 的 _build_system_prompt()
    interactive: bool = True,                # False = 子 agent 模式，end_turn 直接返回
    hook_registry: HookRegistry = DEFAULT_REGISTRY,
    max_turns: int = 0,                      # 0 = 无限制；子 agent 默认传 30
) -> str | None:
    """
    Layer 3 主循环。

    interactive=True  (主 agent): end_turn → 等用户输入，永远跑下去
    interactive=False (子 agent): end_turn → 返回最后一条回复，结束循环
    """
    api_tools = [t.to_api_format() for t in tools]

    state = {
        "continue_reason": None,
        "full_response": "",
        "last_usage": None,
        "turns": 0,
    }

    while True:
        if max_turns and state["turns"] >= max_turns:
            return state["full_response"] or "ERROR: max_turns reached"
        state["turns"] += 1
        state["full_response"] = ""

        if interactive:
            print("\nAssistant: ", end="", flush=True)

        executor = StreamingToolExecutor(tools, hook_registry=hook_registry)

        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system or _build_system_prompt(),
            messages=messages,
            tools=api_tools if api_tools else [],
        ) as stream:
            async for event in stream:
                text = executor.on_event(event)
                if text:
                    if interactive:
                        print(text, end="", flush=True)
                    state["full_response"] += text

            final = await stream.get_final_message()
            state["last_usage"] = final.usage

        if interactive:
            print()

        # ── continue 点: TOOL_USE ────────────────────────────────────────────
        if final.stop_reason == StopReason.TOOL_USE:
            tool_count = len(executor._tool_order)
            if interactive:
                print(f"\n[Tools] 执行 {tool_count} 个工具调用（streaming 模式）...")

            # finish() 先于 append assistant 消息，保证 AgentTool deepcopy 时
            # messages 末尾是干净的（无未结算的 tool_use block）
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

        if not interactive:
            # 子 agent 模式：返回结果给 AgentTool.call()
            return state["full_response"]

        # 主 agent 模式：等用户输入
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
