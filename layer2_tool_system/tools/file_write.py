import os
from .base import Tool


class FileWriteTool(Tool):
    name = "file_write"
    description = "将内容写入文件。如果文件不存在则创建，存在则覆盖。"
    is_read_only = False          # 会写，需要 permission check
    is_destructive = False        # 覆盖不算破坏性，但 is_read_only=False 已触发检查
    is_concurrency_safe = False   # 并发写同一文件会竞争，串行

    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要写入的内容"},
        },
        "required": ["path", "content"],
    }

    async def call(self, input: dict) -> str:
        try:
            os.makedirs(os.path.dirname(input["path"]) or ".", exist_ok=True)
            with open(input["path"], "w", encoding="utf-8") as f:
                f.write(input["content"])
            return f"OK: wrote {len(input['content'])} chars to {input['path']}"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
