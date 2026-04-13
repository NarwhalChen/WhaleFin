"""
TodoWrite / TodoRead

任务列表存 ~/.whalefin/todo.json，结构：
[{"id": 1, "content": "...", "status": "pending|in_progress|completed", "priority": "high|medium|low"}]

对齐 CC 的 TodoWrite/TodoRead 设计。
"""

import json
from pathlib import Path
from .base import Tool

TODO_PATH = Path.home() / ".whalefin" / "todo.json"


def _load() -> list:
    if not TODO_PATH.exists():
        return []
    return json.loads(TODO_PATH.read_text(encoding="utf-8"))


def _save(todos: list) -> None:
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    TODO_PATH.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")


class TodoWriteTool(Tool):
    name = "todo_write"
    description = "写入/替换整个 todo 列表。传入完整的 todos 数组覆盖当前列表。"
    is_read_only = False
    is_concurrency_safe = False

    input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "完整的 todo 列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "id":       {"type": "integer"},
                        "content":  {"type": "string"},
                        "status":   {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["id", "content", "status", "priority"],
                },
            },
        },
        "required": ["todos"],
    }

    async def call(self, input: dict) -> str:
        todos = input["todos"]
        _save(todos)
        return f"Saved {len(todos)} todos."


class TodoReadTool(Tool):
    name = "todo_read"
    description = "读取当前 todo 列表。"
    is_read_only = True
    is_concurrency_safe = True

    input_schema = {
        "type": "object",
        "properties": {},
    }

    async def call(self, input: dict) -> str:
        todos = _load()
        if not todos:
            return "(no todos)"
        return json.dumps(todos, ensure_ascii=False, indent=2)
