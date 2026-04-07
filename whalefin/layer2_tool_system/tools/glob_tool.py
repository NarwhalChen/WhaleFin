import glob
from .base import Tool


class GlobTool(Tool):
    name = "glob"
    description = "用 glob 模式匹配文件路径，例如 '**/*.py'。只读操作。"
    is_read_only = True           # 只查找，不修改
    is_concurrency_safe = True    # 纯读操作，可并发

    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，例如 '**/*.py'"},
        },
        "required": ["pattern"],
    }

    async def call(self, input: dict) -> str:
        try:
            matches = glob.glob(input["pattern"], recursive=True)
            if not matches:
                return "(no matches)"
            return "\n".join(sorted(matches))
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
