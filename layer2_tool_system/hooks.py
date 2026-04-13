"""
PreToolUse / PostToolUse Hook 系统

两类 hook，直接注册到 HookRegistry，tool_execution.py 在 pipeline 里调用：
  validate → [PreToolUse] → permission_check → execute → [PostToolUse]

PreToolUse 返回 HookResult:
  - ALLOW      — 继续正常流程
  - BLOCK      — 短路，返回 error，不执行，不弹 permission
  - AUTO_APPROVE — 跳过 permission_check，直接 execute（CI 模式）

PostToolUse 拿到结果，做副作用（打印/日志），原样返回或改写结果。
"""

from dataclasses import dataclass, field
from typing import Literal


# ── HookResult ───────────────────────────────────────────────────────────────

@dataclass
class HookResult:
    action: Literal["allow", "block", "auto_approve"]
    reason: str = ""

    @staticmethod
    def allow() -> "HookResult":
        return HookResult(action="allow")

    @staticmethod
    def block(reason: str) -> "HookResult":
        return HookResult(action="block", reason=reason)

    @staticmethod
    def auto_approve() -> "HookResult":
        return HookResult(action="auto_approve")


# ── Hook 基类 ─────────────────────────────────────────────────────────────────

class PreToolUseHook:
    async def pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        return HookResult.allow()


class PostToolUseHook:
    async def post_tool_use(self, tool_name: str, tool_args: dict, result: str) -> str:
        return result


# ── HookRegistry ─────────────────────────────────────────────────────────────

def _matches(matcher: str, tool_name: str) -> bool:
    """matcher 支持精确匹配和 * 通配符。"""
    return matcher == "*" or matcher == tool_name


@dataclass
class HookRegistry:
    # (matcher, hook) 元组，matcher 决定哪些 tool 触发这个 hook
    pre_hooks: list[tuple[str, PreToolUseHook]] = field(default_factory=list)
    post_hooks: list[tuple[str, PostToolUseHook]] = field(default_factory=list)

    def register_pre(self, hook: PreToolUseHook, matcher: str = "*") -> None:
        self.pre_hooks.append((matcher, hook))

    def register_post(self, hook: PostToolUseHook, matcher: str = "*") -> None:
        self.post_hooks.append((matcher, hook))

    async def run_pre(self, tool_name: str, tool_args: dict) -> HookResult:
        """
        顺序跑匹配的 pre hook。
        - BLOCK 立刻短路（对齐 CC：用户 hook 可以推翻 AUTO_APPROVE）
        - AUTO_APPROVE 记录但继续，让后续 hook 仍有机会 BLOCK
        - 最终返回最后一个非 ALLOW 结果，或 ALLOW
        """
        final = HookResult.allow()
        for matcher, hook in self.pre_hooks:
            if not _matches(matcher, tool_name):
                continue
            result = await hook.pre_tool_use(tool_name, tool_args)
            if result.action == "block":
                return result
            if result.action == "auto_approve":
                final = result
        return final

    async def run_post(self, tool_name: str, tool_args: dict, result: str) -> str:
        """顺序跑匹配的 post hook，每个都可以改写 result。"""
        for matcher, hook in self.post_hooks:
            if not _matches(matcher, tool_name):
                continue
            result = await hook.post_tool_use(tool_name, tool_args, result)
        return result


# ── 内置 Hook：执行日志 ────────────────────────────────────────────────────────

class ToolLogger(PostToolUseHook):
    """PostToolUse: 打印每次工具调用的结果摘要。"""

    async def post_tool_use(self, tool_name: str, tool_args: dict, result: str) -> str:
        preview = result[:120].replace("\n", " ")
        if len(result) > 120:
            preview += "..."
        print(f"[Log] {tool_name} → {preview}")
        return result


# ── 默认 registry（空，按需注册）────────────────────────────────────────────────
DEFAULT_REGISTRY = HookRegistry()
