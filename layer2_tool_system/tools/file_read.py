from .base import Tool


class FileReadTool(Tool):
    name = "file_read"
    description = "读取文件内容并返回。只读操作，不修改文件。"
    is_read_only = True           # 只读，跳过 permission check
    is_concurrency_safe = True    # 多个 read 可以并发，互不影响

    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
        },
        "required": ["path"],
    }

    async def call(self, input: dict) -> str:
        try:
            with open(input["path"], "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return f"ERROR: FileNotFoundError: {input['path']} not found"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
