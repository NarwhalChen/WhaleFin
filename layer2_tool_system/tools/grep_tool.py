import asyncio
from .base import Tool


class GrepTool(Tool):
    name = "grep"
    description = "用正则表达式搜索文件内容，返回匹配行及行号。"
    is_read_only = True
    is_concurrency_safe = True

    input_schema = {
        "type": "object",
        "properties": {
            "pattern":     {"type": "string", "description": "正则表达式"},
            "path":        {"type": "string", "description": "搜索目录或文件路径，默认当前目录", "default": "."},
            "glob":        {"type": "string", "description": "文件 glob 过滤，如 '*.py'"},
            "ignore_case": {"type": "boolean", "description": "是否忽略大小写", "default": False},
        },
        "required": ["pattern"],
    }

    async def call(self, input: dict) -> str:
        pattern = input["pattern"]
        path = input.get("path", ".")
        glob = input.get("glob")
        ignore_case = input.get("ignore_case", False)

        cmd = ["rg", "--line-number", "--no-heading"]
        if ignore_case:
            cmd.append("-i")
        if glob:
            cmd += ["--glob", glob]
        cmd += [pattern, path]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            out = stdout.decode().strip()
            if not out:
                return "(no matches)"
            lines = out.splitlines()
            if len(lines) > 200:
                return "\n".join(lines[:200]) + f"\n... ({len(lines) - 200} more lines)"
            return out
        except FileNotFoundError:
            # rg 不存在，fallback 到 grep
            cmd = ["grep", "-rn"]
            if ignore_case:
                cmd.append("-i")
            if glob:
                cmd += ["--include", glob]
            cmd += [pattern, path]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            out = stdout.decode().strip()
            return out if out else "(no matches)"
        except asyncio.TimeoutError:
            return "ERROR: TimeoutError: grep exceeded 15s"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
