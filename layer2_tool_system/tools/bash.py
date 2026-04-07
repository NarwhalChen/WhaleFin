import asyncio
from .base import Tool


class BashTool(Tool):
    name = "bash"
    description = "执行 bash 命令并返回输出。危险操作，会触发 permission check。"
    is_read_only = False          # 执行命令可能写文件、删文件
    is_destructive = False        # 具体是否破坏性取决于命令，Layer 5 会细化
    is_concurrency_safe = False   # bash 命令可能互相影响，串行

    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 bash 命令"},
        },
        "required": ["command"],
    }

    async def call(self, input: dict) -> str:
        try:
            proc = await asyncio.create_subprocess_shell(
                input["command"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode() + stderr.decode()
            return output.strip() if output.strip() else "(no output)"
        except asyncio.TimeoutError:
            return "ERROR: TimeoutError: command exceeded 30s"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
