"""
Task 生命周期工具

把 BackgroundManager 里的 TaskRecord 暴露给 Claude：
- task_get    — 查询单个任务状态和结果
- task_list   — 列出所有任务摘要
- task_stop   — 取消运行中的任务
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from layer2_tool_system.tools.base import Tool
from layer3_multi_agent.background import BackgroundManager


class TaskGetTool(Tool):
    name = "task_get"
    description = "查询后台任务的状态和结果。status: running/done/error"
    is_read_only = True
    is_concurrency_safe = True

    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID，如 bg_1"},
        },
        "required": ["task_id"],
    }

    def __init__(self, bg_manager: BackgroundManager):
        self.bg_manager = bg_manager

    async def call(self, input: dict) -> str:
        task_id = input["task_id"]
        record = self.bg_manager._tasks.get(task_id)
        if record is None:
            return f"ERROR: task '{task_id}' not found"
        return (
            f"task_id: {record.task_id}\n"
            f"agent:   {record.agent_type}\n"
            f"status:  {record.status}\n"
            f"task:    {record.task_desc[:80]}\n"
            f"result:  {record.result[:300] if record.result else '(pending)'}"
        )


class TaskListTool(Tool):
    name = "task_list"
    description = "列出所有后台任务及其状态摘要"
    is_read_only = True
    is_concurrency_safe = True

    input_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, bg_manager: BackgroundManager):
        self.bg_manager = bg_manager

    async def call(self, input: dict) -> str:
        if not self.bg_manager._tasks:
            return "没有后台任务"
        lines = []
        for record in self.bg_manager._tasks.values():
            lines.append(
                f"[{record.task_id}] {record.status:7} | {record.agent_type} | {record.task_desc[:50]}"
            )
        return "\n".join(lines)


class TaskStopTool(Tool):
    name = "task_stop"
    description = "取消运行中的后台任务"
    is_read_only = False
    is_concurrency_safe = False

    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "要取消的任务 ID"},
        },
        "required": ["task_id"],
    }

    def __init__(self, bg_manager: BackgroundManager):
        self.bg_manager = bg_manager

    async def call(self, input: dict) -> str:
        task_id = input["task_id"]
        record = self.bg_manager._tasks.get(task_id)
        if record is None:
            return f"ERROR: task '{task_id}' not found"
        if record.status != "running":
            return f"task '{task_id}' is already {record.status}"

        asyncio_task = self.bg_manager._asyncio_tasks.get(task_id)
        if asyncio_task:
            asyncio_task.cancel()

        record.status = "error"
        record.result = "cancelled by user"
        return f"task '{task_id}' cancelled"


def make_task_tools(bg_manager: BackgroundManager) -> list:
    return [
        TaskGetTool(bg_manager),
        TaskListTool(bg_manager),
        TaskStopTool(bg_manager),
    ]
