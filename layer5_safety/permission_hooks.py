"""
PermissionMode Hooks

四种模式，全部通过 PreToolUseHook 实现，注册进 HookRegistry：

  default  → DefaultModeHook: is_read_only → AUTO_APPROVE，否则弹窗（ASK）
  plan     → PlanModeHook: 写操作 BLOCK，读操作 AUTO_APPROVE
  auto     → AutoModeHook: 读操作 AUTO_APPROVE，写操作 ALLOW（继续走弹窗）
  --dangerously-skip-permissions → SkipPermissionsHook: 全部 AUTO_APPROVE

启动时按 args 选择注册哪个 hook，不改 pipeline。
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from layer2_tool_system.hooks import PreToolUseHook, HookResult


class DefaultModeHook(PreToolUseHook):
    """
    default 模式：复现原来 _permission_check 的行为。
    is_read_only → AUTO_APPROVE（跳过弹窗）
    否则 → ALLOW（继续走 permission 弹窗）
    """
    def __init__(self, tool_registry: dict):
        self._tool_registry = tool_registry  # name → Tool

    async def pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        tool = self._tool_registry.get(tool_name)
        if tool and tool.is_read_only:
            return HookResult.auto_approve()
        return HookResult.allow()


class PlanModeHook(PreToolUseHook):
    """
    plan 模式：只读。写操作全部 BLOCK，读操作 AUTO_APPROVE。
    """
    def __init__(self, tool_registry: dict):
        self._tool_registry = tool_registry

    async def pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        tool = self._tool_registry.get(tool_name)
        if tool and not tool.is_read_only:
            return HookResult.block(f"plan mode: '{tool_name}' is a write operation")
        return HookResult.auto_approve()


class AutoModeHook(PreToolUseHook):
    """
    auto 模式：读操作 AUTO_APPROVE，写操作继续走 permission 弹窗。
    """
    def __init__(self, tool_registry: dict):
        self._tool_registry = tool_registry

    async def pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        tool = self._tool_registry.get(tool_name)
        if tool and tool.is_read_only:
            return HookResult.auto_approve()
        return HookResult.allow()


class SkipPermissionsHook(PreToolUseHook):
    """
    --dangerously-skip-permissions: 全部 AUTO_APPROVE，用于 CI 环境。
    """
    async def pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        return HookResult.auto_approve()


def register_permission_mode(registry, mode: str, tools: list) -> None:
    """
    按 mode 注册对应的 permission hook。
    在 BashClassifier 之后注册，permission 决策在安全检查之后发生。
    """
    tool_registry = {t.name: t for t in tools}

    if mode == "skip":
        registry.register_pre(SkipPermissionsHook(), matcher="*")
    elif mode == "plan":
        registry.register_pre(PlanModeHook(tool_registry), matcher="*")
    elif mode == "auto":
        registry.register_pre(AutoModeHook(tool_registry), matcher="*")
    else:  # default
        registry.register_pre(DefaultModeHook(tool_registry), matcher="*")
