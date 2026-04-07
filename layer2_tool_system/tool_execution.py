"""
工具执行 Pipeline

每个工具调用走: validate → permission_check → execute
错误在每一步都返回 "ERROR: ..." 字符串，不抛异常，主循环不崩。

并发设计:
- 按 is_concurrency_safe 分组
- safe 组: asyncio.gather 并发跑
- unsafe 组: 串行逐个跑
- 关键: 用 orderlist (results = [None] * n) 保证返回顺序和输入顺序一致
  不能直接 safe_results + unsafe_results 拼接，顺序会乱
  Claude 的 tool_result 必须和 tool_use 的 id 一一对应
"""

import asyncio
from .tools.base import Tool


def _build_registry(tools: list[Tool]) -> dict[str, Tool]:
    return {t.name: t for t in tools}


async def _permission_check(tool: Tool, tool_name: str, tool_args: dict) -> bool:
    """
    只读工具跳过。非只读工具弹窗询问用户。
    返回 True = 允许，False = 拒绝。
    """
    if tool.is_read_only:
        return True

    print(f"\n[Permission] 工具: {tool_name}")
    print(f"             参数: {tool_args}")
    try:
        answer = input("             允许执行? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "y"


async def _run_single(block, tool: Tool | None) -> tuple[str, str]:
    """
    单个工具的完整 pipeline: validate → permission → execute
    返回 (tool_use_id, result_string)
    """
    tool_name = block.name
    tool_args = block.input
    tool_id = block.id

    # 1. 检查工具是否存在
    if tool is None:
        return tool_id, f"ERROR: unknown tool '{tool_name}'"

    # 2. Validate: 检查必填字段
    error = tool.validate(tool_args)
    if error:
        return tool_id, error

    # 3. Permission check
    allowed = await _permission_check(tool, tool_name, tool_args)
    if not allowed:
        return tool_id, "ERROR: tool call denied by user"

    # 4. Execute
    try:
        result = await tool.call(tool_args)
        return tool_id, result
    except Exception as e:
        return tool_id, f"ERROR: {type(e).__name__}: {e}"


async def run_tools(tool_use_blocks: list, tools: list[Tool]) -> list[tuple[str, str]]:
    """
    接收 Claude 返回的所有 tool_use blocks，执行并返回有序结果。

    返回: [(tool_use_id, result_string), ...]
    顺序和 tool_use_blocks 输入顺序一致。
    """
    registry = _build_registry(tools)
    n = len(tool_use_blocks)

    # orderlist: 预分配结果槽，保证输出顺序和输入一致
    results: list[tuple[str, str] | None] = [None] * n

    # 按 concurrency_safe 分组，同时记录原始 index
    safe_indexed = []
    unsafe_indexed = []

    for i, block in enumerate(tool_use_blocks):
        tool = registry.get(block.name)
        if tool and tool.is_concurrency_safe:
            safe_indexed.append((i, block, tool))
        else:
            unsafe_indexed.append((i, block, tool))

    # Safe 组: 并发执行
    if safe_indexed:
        safe_coros = [_run_single(block, tool) for _, block, tool in safe_indexed]
        safe_results = await asyncio.gather(*safe_coros)
        for (i, _, _), (tool_id, result) in zip(safe_indexed, safe_results):
            results[i] = (tool_id, result)

    # Unsafe 组: 串行执行
    for i, block, tool in unsafe_indexed:
        tool_id, result = await _run_single(block, tool)
        results[i] = (tool_id, result)

    return results  # type: ignore
