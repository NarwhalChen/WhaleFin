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
import platform
from pathlib import Path
from anthropic import AsyncAnthropic

# 从项目根目录的 .env 加载 API key
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096

# ── System prompt 静/动分区 ──────────────────────────────────────────────────
# API 对 system prompt 做前缀缓存：前缀字节级一致才命中，命中后跳过 token 处理
# 设计：把永远不变的内容放 BOUNDARY 之前（缓存命中），会变的放后面（不破坏前缀）
DYNAMIC_BOUNDARY = "\n\n---DYNAMIC---\n"

_STATIC_PROMPT = """\
You are a helpful coding assistant.

## Behavior Rules
- Read code before modifying it.
- Do not add unrequested features or abstractions.
- Do not add comments to code you didn't change.
- Report results honestly; don't claim success without verification.
- If an approach fails, diagnose before retrying.\
"""

def _build_system_prompt() -> str:
    dynamic = (
        f"cwd: {Path.cwd()}\n"
        f"os: {platform.system()} {platform.release()}\n"
        f"shell: {os.environ.get('SHELL', 'unknown')}\n"
        f"model: {MODEL}"
    )
    return _STATIC_PROMPT + DYNAMIC_BOUNDARY + dynamic

# ── Continue 点常量 ──────────────────────────────────────────────────────────
# 每个 continue 对应"为什么再跑一轮"的原因，随 layer 增加而扩展
# Layer 2 加: TOOL_USE
# Layer 4 加: REACTIVE_COMPACT
class StopReason:
    END_TURN   = "end_turn"    # 正常结束
    MAX_TOKENS = "max_tokens"  # 输出被截断，自动续写
    TOOL_USE   = "tool_use"    # 有工具调用（Layer 2+ 实现）


async def run_loop(
    messages: list,
    tools: list = [],        # Layer 1 无工具，默认空列表；Layer 2+ 传入实际工具
    client: AsyncAnthropic = None,
) -> None:
    """
    主循环状态机。

    state dict 在迭代之间传递运行时状态，取代散落的局部变量。
    价值: 可观测（任何时候 print(state) 知道循环在哪个阶段）、
         可序列化（未来 session 恢复直接 json.dump(state)）。

    continue 点:
      MAX_TOKENS — 输出被截断，注入续写信号，对用户透明
      END_TURN   — 等用户输入，进入下一轮
      TOOL_USE   — Layer 2+ 实现
    """
    # state dict: 循环的运行时状态
    # 新增字段在这里声明，不要在 while 里随手创建局部变量
    state = {
        "continue_reason": None,  # 上一次 continue 的原因
        "full_response": "",      # 当前轮的累积回复
        "last_usage": None,       # 上一轮 API 的 token 用量（Layer 4 compact 触发用）
    }

    while True:
        state["full_response"] = ""
        print("\nAssistant: ", end="", flush=True)

        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_build_system_prompt(),
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                print(text, end="", flush=True)
                state["full_response"] += text

            final = await stream.get_final_message()
            state["last_usage"] = final.usage

        print()

        # ── continue 点: MAX_TOKENS ──────────────────────────────────────────
        # 输出被 MAX_TOKENS 截断：保存已有内容，注入续写信号，继续循环
        # 不等用户输入，对用户透明
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
