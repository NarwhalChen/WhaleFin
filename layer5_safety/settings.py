"""
配置驱动 Hook 系统

从 settings.json 加载用户自定义 hook，包装成 CommandHook 注册进 HookRegistry。

加载优先级（项目级覆盖用户级）:
  1. {cwd}/.whalefin/settings.json  — 项目级
  2. ~/.whalefin/settings.json      — 用户级

settings.json 格式:
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "bash", "command": "~/.whalefin/hooks/my_checker.sh"}
    ],
    "PostToolUse": [
      {"matcher": "*", "command": "~/.whalefin/hooks/audit.sh"}
    ]
  }
}

Hook 脚本通过 stdin 接收 JSON:
  {"tool": "bash", "args": {"command": "ls"}}

退出码:
  0 → ALLOW
  1 → BLOCK（stdout 作为 reason）
  2 → AUTO_APPROVE
"""

import asyncio
import json
import os
from pathlib import Path

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from layer2_tool_system.hooks import (
    HookRegistry, HookResult,
    PreToolUseHook, PostToolUseHook,
)


# ── CommandHook ───────────────────────────────────────────────────────────────

class CommandPreHook(PreToolUseHook):
    """把外部 shell 命令包装成 PreToolUseHook。"""

    def __init__(self, command: str):
        self.command = os.path.expanduser(command)

    async def pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        payload = json.dumps({"tool": tool_name, "args": tool_args})
        try:
            proc = await asyncio.create_subprocess_shell(
                self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate(input=payload.encode())
            exit_code = proc.returncode
        except Exception as e:
            return HookResult.allow()   # hook 执行失败，fail-open，不阻断

        if exit_code == 1:
            reason = stdout.decode().strip() or "blocked by hook"
            return HookResult.block(reason)
        elif exit_code == 2:
            return HookResult.auto_approve()
        return HookResult.allow()


class CommandPostHook(PostToolUseHook):
    """把外部 shell 命令包装成 PostToolUseHook。"""

    def __init__(self, command: str):
        self.command = os.path.expanduser(command)

    async def post_tool_use(self, tool_name: str, tool_args: dict, result: str) -> str:
        payload = json.dumps({"tool": tool_name, "args": tool_args, "result": result})
        try:
            proc = await asyncio.create_subprocess_shell(
                self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate(input=payload.encode())
        except Exception:
            pass   # post hook 失败不影响结果
        return result


# ── 配置加载 ──────────────────────────────────────────────────────────────────

def _load_settings() -> dict:
    """加载 settings.json，项目级优先于用户级，缺失字段用用户级补全。"""
    user_settings = Path.home() / ".whalefin" / "settings.json"
    project_settings = Path.cwd() / ".whalefin" / "settings.json"

    merged: dict = {}

    for path in [user_settings, project_settings]:   # 后者覆盖前者
        if path.exists():
            try:
                data = json.loads(path.read_text())
                merged.update(data)
            except (json.JSONDecodeError, OSError):
                pass

    return merged


def load_hooks_into(registry: HookRegistry) -> None:
    """
    读取 settings.json，把用户配置的 hook 追加进 registry。
    内置 hook（BashClassifier 等）已经在 main() 里注册，这里只加用户 hook。
    """
    settings = _load_settings()
    hooks_config = settings.get("hooks", {})

    for entry in hooks_config.get("PreToolUse", []):
        matcher = entry.get("matcher", "*")
        command = entry.get("command", "")
        if command:
            registry.register_pre(CommandPreHook(command), matcher=matcher)

    for entry in hooks_config.get("PostToolUse", []):
        matcher = entry.get("matcher", "*")
        command = entry.get("command", "")
        if command:
            registry.register_post(CommandPostHook(command), matcher=matcher)
