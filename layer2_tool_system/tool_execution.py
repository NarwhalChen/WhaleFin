"""
工具执行 Pipeline

每个工具调用走: validate → permission_check → execute
错误在每一步都返回 "ERROR: ..." 字符串，不抛异常，主循环不崩。

两种执行模式:
1. run_tools()            — 批量模式，收齐所有 block 再执行（Layer 2 原版）
2. StreamingToolExecutor  — 流式模式，safe block 完整立刻执行，unsafe 攒起来等 safe 结束再串行
                            对应 CC 的 StreamingToolExecutor.ts

并发设计:
- 按 is_concurrency_safe 分组
- safe 组: asyncio.gather 并发跑
- unsafe 组: 串行逐个跑
- 关键: 用 orderlist 保证返回顺序和输入顺序一致
  Claude 的 tool_result 必须和 tool_use 的 id 一一对应
"""

import asyncio
import json
from .tools.base import Tool
from .hooks import HookRegistry, DEFAULT_REGISTRY


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


async def _run_single_dict(
    block_dict: dict,
    tool: Tool | None,
    registry: HookRegistry = DEFAULT_REGISTRY,
) -> tuple[str, str]:
    """
    和 _run_single 相同的 pipeline，但接收 dict 而不是 SDK block 对象。
    StreamingToolExecutor 在流式事件里自己构建 block dict，无法使用 SDK 对象。

    pipeline: validate → PreToolUse → permission_check → execute → PostToolUse
    """
    tool_name = block_dict["name"]
    tool_args = block_dict["input"]
    tool_id = block_dict["id"]

    if tool is None:
        return tool_id, f"ERROR: unknown tool '{tool_name}'"

    error = tool.validate(tool_args)
    if error:
        return tool_id, error

    # PreToolUse hooks: BLOCK 短路，AUTO_APPROVE 跳过 permission
    hook_result = await registry.run_pre(tool_name, tool_args)
    if hook_result.action == "block":
        return tool_id, f"ERROR: blocked by hook — {hook_result.reason}"

    if hook_result.action != "auto_approve":
        allowed = await _permission_check(tool, tool_name, tool_args)
        if not allowed:
            return tool_id, "ERROR: tool call denied by user"

    try:
        result = await tool.call(tool_args)
    except Exception as e:
        return tool_id, f"ERROR: {type(e).__name__}: {e}"

    # PostToolUse hooks: 副作用（日志/截断），可改写 result
    result = await registry.run_post(tool_name, tool_args, result)
    return tool_id, result


class StreamingToolExecutor:
    """
    边接收流式输出边执行工具。对应 CC 的 StreamingToolExecutor.ts。

    策略（方案 C）:
    - safe 工具: content_block_stop 事件触发时立刻 create_task
    - unsafe 工具: 攒起来，等所有 safe task 完成后串行跑
    - 返回顺序和 tool_use block 出现顺序一致（orderlist 模式）

    用法:
        executor = StreamingToolExecutor(tools)
        async for event in stream:
            executor.on_event(event)         # 处理事件，safe 工具自动开跑
        ordered_results = await executor.finish()  # 等 safe，跑 unsafe，返回结果
    """

    def __init__(self, tools: list[Tool], hook_registry: HookRegistry = DEFAULT_REGISTRY):
        self._registry = _build_registry(tools)
        self._hook_registry = hook_registry
        self._blocks: dict[int, dict] = {}           # stream_index → 正在构建的 block
        self._tool_order: list[int] = []             # tool_use block 出现的 stream_index 顺序
        self._safe_tasks: list[tuple[int, asyncio.Task]] = []   # (stream_index, task)
        self._unsafe_pending: list[tuple[int, dict]] = []       # (stream_index, completed block)
        self._results: dict[int, tuple[str, str]] = {}          # stream_index → result

    def on_event(self, event) -> str | None:
        """
        处理单个流事件。
        返回文本 delta（如果有），供调用方打印。
        """
        t = event.type

        if t == "content_block_start":
            cb = event.content_block
            if cb.type == "text":
                self._blocks[event.index] = {"type": "text", "text": ""}
            elif cb.type == "tool_use":
                self._blocks[event.index] = {
                    "type": "tool_use",
                    "id": cb.id,
                    "name": cb.name,
                    "input_raw": "",
                }
                self._tool_order.append(event.index)

        elif t == "content_block_delta":
            block = self._blocks.get(event.index)
            if block is None:
                return None
            delta = event.delta
            if delta.type == "text_delta":
                block["text"] += delta.text
                return delta.text          # 调用方负责打印
            elif delta.type == "input_json_delta":
                block["input_raw"] += delta.partial_json

        elif t == "content_block_stop":
            block = self._blocks.get(event.index)
            if block and block["type"] == "tool_use":
                block["input"] = json.loads(block["input_raw"]) if block["input_raw"] else {}
                tool = self._registry.get(block["name"])
                if tool and tool.is_concurrency_safe:
                    # safe: 立刻开跑
                    task = asyncio.create_task(self._run_safe(event.index, block, tool))
                    self._safe_tasks.append((event.index, task))
                else:
                    # unsafe: 攒起来
                    self._unsafe_pending.append((event.index, block, tool))

        return None

    async def _run_safe(self, index: int, block: dict, tool: Tool) -> None:
        result = await _run_single_dict(block, tool, self._hook_registry)
        self._results[index] = result

    async def finish(self) -> list[tuple[str, str]]:
        """
        等所有 safe task 完成（gather = C 的 join），
        再串行跑 unsafe，最后按 tool_order 返回有序结果。
        """
        if self._safe_tasks:
            await asyncio.gather(*[task for _, task in self._safe_tasks])

        for index, block, tool in self._unsafe_pending:
            result = await _run_single_dict(block, tool, self._hook_registry)
            self._results[index] = result

        return [self._results[idx] for idx in self._tool_order]


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
