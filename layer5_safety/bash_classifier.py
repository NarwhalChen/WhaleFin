"""
BashClassifier — PreToolUseHook

bash 命令在进入 permission pipeline 之前先做静态分析。
匹配 dangerousPatterns → BLOCK，不弹窗，不走 permission check。

注册方式:
    registry.register_pre(BashClassifier())
"""

import re
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from layer2_tool_system.hooks import PreToolUseHook, HookResult


# 危险模式：(pattern, reason)
# 按严重程度排列，第一个匹配就短路
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # 破坏性文件操作
    (r"rm\s+-[a-z]*r[a-z]*f|rm\s+-[a-z]*f[a-z]*r", "recursive force delete"),
    (r">\s*/dev/(sda|hda|nvme|disk)",               "writing to raw disk device"),
    (r"mkfs\.",                                      "filesystem format"),
    (r"dd\s+.*of=/dev/",                             "dd to block device"),

    # 远程代码执行
    (r"curl\s+.*\|\s*(bash|sh|python|ruby|perl)",   "remote code execution via pipe"),
    (r"wget\s+.*\|\s*(bash|sh|python|ruby|perl)",   "remote code execution via pipe"),
    (r"eval\s*\$\(",                                 "eval with command substitution"),

    # 权限提升
    (r"\bsudo\b",                                    "sudo privilege escalation"),
    (r"\bsu\s+-",                                    "su privilege escalation"),
    (r"chmod\s+[0-7]*7[0-7]*\s+/",                  "world-writable on root path"),

    # 系统破坏
    (r":\s*\(\s*\)\s*\{.*:\|:&\s*\}",               "fork bomb"),
    (r"shutdown|reboot|halt|poweroff",               "system shutdown"),

    # 历史清除（掩盖痕迹）
    (r"history\s+-[cw]",                             "clearing shell history"),
    (r">\s*~/\.(bash|zsh)_history",                  "overwriting shell history"),
]

# 编译一次，复用
_COMPILED = [(re.compile(p, re.IGNORECASE), reason) for p, reason in DANGEROUS_PATTERNS]


class BashClassifier(PreToolUseHook):
    """
    只拦截 bash 工具，其他工具直接 ALLOW。
    匹配任意一条 dangerousPattern → BLOCK，返回原因。
    """

    async def pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        if tool_name != "bash":
            return HookResult.allow()

        command = tool_args.get("command", "")

        for pattern, reason in _COMPILED:
            if pattern.search(command):
                return HookResult.block(f"dangerous pattern detected: {reason}")

        return HookResult.allow()
