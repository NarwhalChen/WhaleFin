"""
Agent 配置表

每个 agent = system_prompt + tools
client 和 messages 在运行时传入，不存在这里。

新增 agent：在 AGENT_CONFIGS 里加一条，AgentTool 的 description 会自动同步。
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from layer2_tool_system.tools.file_read import FileReadTool
from layer2_tool_system.tools.glob_tool import GlobTool

AGENT_CONFIGS = {
    "explore": {
        "system": (
            "你是一个只读代码探索专家。"
            "你只能读取文件和搜索文件路径，不能写入或执行任何命令。"
            "分析代码结构，回答关于代码库的问题。"
        ),
        "tools": [FileReadTool(), GlobTool()],
    },
    "verify": {
        "system": (
            "你是一个文件验证专家。"
            "用户给你一个或多个文件路径，你用 file_read 工具逐一检查每个文件是否存在且非空。"
            "你的回复必须以 PASS 或 FAIL 开头，然后说明原因。"
        ),
        "tools": [FileReadTool()],
    },
}
