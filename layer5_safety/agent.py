"""
Layer 5: Safety

在 Layer 4 基础上新增:
- BashClassifier: 危险命令检测 PreToolUseHook
- matcher-based HookRegistry: hook 按工具名匹配
- config-driven hooks: ~/.whalefin/settings.json + .whalefin/settings.json
- PermissionMode: default/plan/auto/skip 四种模式，全部通过 PreToolUseHook 实现
- Session Persistence: JSONL append-only，~/.whalefin/sessions/{id}.jsonl

设计决策:
- PermissionMode 作为 hook 注册，不改 pipeline，控制反转
- 内置 hook（BashClassifier）永远先跑，用户配置无法覆盖
- JSONL append-only: crash-safe，写一半不损坏历史
"""

import asyncio
import sys
import os
import random
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()

from anthropic import AsyncAnthropic
from layer1_main_loop.agent import _build_system_prompt, StopReason, AGENT_BOUNDARY
from layer2_tool_system.tools import ALL_TOOLS
from layer2_tool_system.tool_execution import StreamingToolExecutor
from layer2_tool_system.hooks import HookRegistry, DEFAULT_REGISTRY, ToolLogger
from layer3_multi_agent.agents.agent_tool import AgentTool
from layer3_multi_agent.background import BackgroundManager
from layer3_multi_agent.tools.task_tools import make_task_tools
from layer4_context_economy.tools.compact_tool import CompactTool
from layer4_context_economy.compaction import snip_result, MicroCompactor
from layer5_safety.bash_classifier import BashClassifier
from layer5_safety.settings import load_hooks_into
from layer5_safety.permission_hooks import register_permission_mode
from layer5_safety.session import new_session_id, append_message, load_session, resolve_session_id, mark_active

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096
COMPACT_THRESHOLD = 0.8  # token 用量超过 80% 触发自动压缩

COMPACT_RULES = """
## Context Economy Rules
当 token 用量超过上限的 80% 时，调用 compact 工具压缩历史。
compact 工具必须单独调用，不能与其他工具并行。
看到 <summary> 标签时，这是之前对话的压缩摘要，不是用户消息，直接当作历史上下文使用。
"""


_RETRYABLE = ("429", "529", "overloaded", "rate limit", "timeout", "connection")
_MAX_RETRIES = 5


async def _with_retry(fn, max_retries: int = _MAX_RETRIES):
    """指数退避重试，处理 429/529/网络瞬态错误。"""
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            msg = str(e).lower()
            if attempt < max_retries and any(k in msg for k in _RETRYABLE):
                wait = (2 ** attempt) + random.random()
                print(f"\n[Retry] {type(e).__name__}，{wait:.1f}s 后重试（{attempt+1}/{max_retries}）...")
                await asyncio.sleep(wait)
            else:
                raise


async def run_loop(
    messages: list,
    tools: list = [],
    client: AsyncAnthropic = None,
    system: str = None,                      # None → _build_system_prompt() + COMPACT_RULES
    interactive: bool = True,
    hook_registry: HookRegistry = DEFAULT_REGISTRY,
    max_turns: int = 0,
    bg_manager: BackgroundManager = None,
    session_id: str = None,
) -> str | None:

    api_tools = [t.to_api_format() for t in tools]
    compact_tool = next((t for t in tools if t.name == "compact"), None)

    state = {
        "continue_reason": None,
        "full_response": "",
        "last_usage": None,
        "turns": 0,
    }
    micro = MicroCompactor()

    while True:
        if max_turns and state["turns"] >= max_turns:
            return state["full_response"] or "ERROR: max_turns reached"
        state["turns"] += 1

        if bg_manager:
            bg_manager.drain_into(messages)

        state["full_response"] = ""

        if interactive:
            print("\nAssistant: ", end="", flush=True)

        executor = StreamingToolExecutor(tools, hook_registry=hook_registry)

        # Reactive Compact: 每个 turn 只尝试一次，防止 413 无限循环
        reactive_attempted = False

        async def _do_stream():
            async with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system or (_build_system_prompt() + AGENT_BOUNDARY + COMPACT_RULES),
                messages=messages,
                tools=api_tools if api_tools else [],
            ) as stream:
                async for event in stream:
                    text = executor.on_event(event)
                    if text:
                        if interactive:
                            print(text, end="", flush=True)
                        state["full_response"] += text
                return await stream.get_final_message()

        try:
            final = await _with_retry(_do_stream)
        except Exception as e:
            if "prompt is too long" in str(e).lower() or "413" in str(e):
                if not reactive_attempted and compact_tool:
                    reactive_attempted = True
                    print("\n[Reactive Compact] prompt too long，紧急压缩后重试...")
                    await compact_tool.call({})
                    state["full_response"] = ""
                    executor = StreamingToolExecutor(tools, hook_registry=hook_registry)
                    final = await _with_retry(_do_stream)
                else:
                    raise
            else:
                raise

        state["last_usage"] = final.usage

        if interactive:
            print()

        # token 监控：处理 response 前先检查是否需要压缩
        if compact_tool and final.usage.input_tokens / MAX_TOKENS > COMPACT_THRESHOLD:
            print(f"\n[Auto-compact] token {final.usage.input_tokens}/{MAX_TOKENS} ({final.usage.input_tokens/MAX_TOKENS:.0%})，自动压缩...")
            result = await compact_tool.call({})
            print(f"[Auto-compact] {result}")

        # ── continue 点: TOOL_USE ────────────────────────────────────────────
        if final.stop_reason == StopReason.TOOL_USE:
            tool_count = len(executor._tool_order)
            if interactive:
                print(f"\n[Tools] 执行 {tool_count} 个工具调用（streaming 模式）...")

            ordered_results = await executor.finish()

            messages.append({"role": "assistant", "content": final.content})
            tool_results = []
            for tool_id, result in ordered_results:
                # Snip: 超长结果存磁盘，只保留 preview
                result = snip_result(tool_id, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result,
                })
            messages.append({"role": "user", "content": tool_results})
            # Micro: 窗口滑动，被挤出的旧结果替换成占位符
            for tool_id, _ in ordered_results:
                micro.add(tool_id, messages)
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
            if session_id:
                append_message(session_id, {"role": "assistant", "content": state["full_response"]})

        if not interactive:
            return state["full_response"]

        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[退出]")
            break

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        if session_id:
            append_message(session_id, {"role": "user", "content": user_input})
        state["continue_reason"] = StopReason.END_TURN


async def main(permission_mode: str = "default", resume: str = None) -> None:
    client = AsyncAnthropic()

    # Session setup
    if resume:
        try:
            session_id = resolve_session_id(resume)
            messages = load_session(session_id)
            if messages is None:
                print(f"[Session] 文件已删除，开新 session")
                session_id = new_session_id()
                messages = []
            else:
                print(f"[Session] Resume: {session_id}（{len(messages)} 条历史）")
            mark_active(session_id)
        except FileNotFoundError as e:
            print(f"[Session] {e}，开新 session")
            session_id = new_session_id()
            messages = []
            mark_active(session_id)
    else:
        session_id = new_session_id()
        messages = []
        mark_active(session_id)
        print(f"[Session] 新会话: {session_id}")

    bg_manager = BackgroundManager()
    compact_tool = CompactTool(client=client, main_messages_ref=messages)
    agent_tool = AgentTool(client=client, main_messages_ref=messages, bg_manager=bg_manager)
    tools = ALL_TOOLS + [agent_tool, compact_tool] + make_task_tools(bg_manager)

    # 内置 hook（永远跑，用户配置覆盖不了）
    registry = HookRegistry()
    registry.register_pre(BashClassifier(), matcher="bash")
    registry.register_post(ToolLogger(), matcher="*")

    # PermissionMode hook（BashClassifier 之后注册，安全检查先于权限决策）
    register_permission_mode(registry, permission_mode, tools)

    # 用户配置 hook（从 settings.json 加载，追加在最后）
    load_hooks_into(registry)

    print(f"[Layer 5] 工具已加载: {[t.name for t in tools]}")
    print(f"[Layer 5] Permission mode: {permission_mode}")
    print(f"[Layer 5] 自动压缩阈值: {COMPACT_THRESHOLD:.0%}")

    try:
        first_input = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    if not first_input:
        return

    messages.append({"role": "user", "content": first_input})
    # 第一条用户消息写入 session
    if session_id:
        append_message(session_id, {"role": "user", "content": messages[-1]["content"]})

    await run_loop(messages, tools, client, bg_manager=bg_manager, hook_registry=registry, session_id=session_id)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--permission-mode",
        choices=["default", "plan", "auto"],
        default="default",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Resume a previous session. Use 'last' for the most recent.",
    )
    args = parser.parse_args()

    mode = "skip" if args.dangerously_skip_permissions else args.permission_mode
    asyncio.run(main(permission_mode=mode, resume=args.resume))
