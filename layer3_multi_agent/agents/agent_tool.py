"""
AgentTool — 把子 agent 包装成普通工具

对 tool_execution.py 来说，这和 FileReadTool 没有区别：
  validate → permission → call() → 返回 string

"特殊"只在 call() 内部：它启动另一个 run_loop(interactive=False)
而不是读文件或执行命令。

client 和 main_messages_ref 在 __init__ 注入：
  - client: 所有 agent 共用同一个 AsyncAnthropic 实例
  - main_messages_ref: 指向主 agent 的 messages list，deepcopy 用于给子 agent context
"""

import copy
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from layer2_tool_system.tools.base import Tool
from layer3_multi_agent.agents.configs import AGENT_CONFIGS


class AgentTool(Tool):
    name = "agent"
    is_read_only = False
    is_concurrency_safe = False

    input_schema = {
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "description": "要调用的 agent 类型，见工具描述中的可用列表",
            },
            "task": {
                "type": "string",
                "description": "给子 agent 的任务描述",
            },
        },
        "required": ["agent_type", "task"],
    }

    def __init__(self, client, main_messages_ref: list):
        self.client = client
        self.main_messages_ref = main_messages_ref  # 指向主 agent 的 messages，不拷贝

        # 动态生成 description，列出所有可用 agent
        agent_list = "\n".join(
            f"- {name}: {config['system'][:50]}..."
            for name, config in AGENT_CONFIGS.items()
        )
        self.description = f"调用专用子 agent 完成特定任务。可用的 agent:\n{agent_list}"

    async def call(self, input: dict) -> str:
        # 延迟 import 避免循环依赖（agent_tool → agent.py → agent_tool）
        from layer3_multi_agent.agent import run_loop

        agent_type = input["agent_type"]
        task = input["task"]

        if agent_type not in AGENT_CONFIGS:
            return f"ERROR: unknown agent type '{agent_type}'，可用: {list(AGENT_CONFIGS.keys())}"

        config = AGENT_CONFIGS[agent_type]

        # deepcopy 主 agent 的历史，让子 agent 知道上下文
        # 不共享引用：子 agent 的写入不会污染主 agent 的 messages
        sub_messages = copy.deepcopy(self.main_messages_ref)
        sub_messages.append({"role": "user", "content": task})

        print(f"\n[Agent] 启动 {agent_type} agent，任务: {task[:60]}...")

        result = await run_loop(
            messages=sub_messages,
            tools=config["tools"],
            client=self.client,
            system=config["system"],
            interactive=False,  # 子 agent：end_turn 时返回结果，不等用户输入
        )

        return result or "ERROR: agent 未返回任何结果"
