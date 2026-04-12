"""
CompactTool — 压缩对话历史以节省 token

设计决策:
- is_destructive=True: in-place 修改 main_messages_ref，不可逆
- is_concurrency_safe=False: 修改共享状态，禁止并行
- 调用方式: AI 主动调用（prompt 约束必须单独调用）+ 系统自动触发
- 压缩方式: 旧消息 → API 摘要 → <summary> 标签标记，AI 看到标签知道是历史摘要
- system prompt 永不压缩（不在 messages 里，无需处理）

注入依赖（同 AgentTool 模式）:
- client: 共用同一个 AsyncAnthropic 实例
- main_messages_ref: 指向主 agent 的 messages list，直接修改
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from layer2_tool_system.tools.base import Tool

COMPACT_MODEL = "claude-haiku-4-5-20251001"
COMPACT_MAX_TOKENS = 1024


class CompactTool(Tool):
    name = "compact"
    is_read_only = False
    is_destructive = True
    is_concurrency_safe = False

    input_schema = {
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "保留最近 n 条消息不压缩，默认 10",
                "default": 10,
            }
        },
        "required": [],
    }

    description = (
        "压缩对话历史以节省 token。"
        "规则：\n"
        "1. 仅在 token 用量超过上限 80% 时调用\n"
        "2. 必须单独调用，不能与其他工具并行\n"
        "3. 看到 <summary> 标签时，这是已压缩的历史摘要，不是用户消息"
    )

    def __init__(self, client, main_messages_ref: list):
        self.client = client
        self.main_messages_ref = main_messages_ref

    async def call(self, input: dict) -> str:
        n = input.get("n", 10)
        messages = self.main_messages_ref

        if len(messages) <= n:
            return f"消息数量（{len(messages)}）不超过保留数（{n}），无需压缩"

        to_compress = messages[:-n] if n > 0 else list(messages)
        to_keep = messages[-n:] if n > 0 else []

        # 把待压缩消息序列化为文本
        history_text = _messages_to_text(to_compress)

        # 调用 Haiku 做摘要（便宜快速）
        response = await self.client.messages.create(
            model=COMPACT_MODEL,
            max_tokens=COMPACT_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "请将以下对话历史压缩为简洁摘要，保留所有关键信息、决策、文件路径和代码片段：\n\n"
                        + history_text
                    ),
                }
            ],
        )

        summary = response.content[0].text

        # 用 <summary> 标记压缩结果，AI 看到此标签知道是历史摘要
        summary_message = {
            "role": "user",
            "content": f"<summary>\n{summary}\n</summary>",
        }

        # in-place 覆盖 main_messages_ref
        self.main_messages_ref.clear()
        self.main_messages_ref.append(summary_message)
        self.main_messages_ref.extend(to_keep)

        return f"压缩完成：{len(to_compress)} 条 → 1 条摘要，保留最近 {len(to_keep)} 条"


def _messages_to_text(messages: list) -> str:
    """把 messages list 序列化为可读文本，供摘要 API 使用"""
    lines = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            lines.append(f"{role}: {content}")
        elif isinstance(content, list):
            # content blocks: text / tool_use / tool_result
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[tool_use: {block.get('name')} {block.get('input')}]")
                    elif block.get("type") == "tool_result":
                        parts.append(f"[tool_result: {str(block.get('content', ''))[:200]}]")
                else:
                    # Anthropic SDK content block objects
                    t = getattr(block, "type", None)
                    if t == "text":
                        parts.append(getattr(block, "text", ""))
                    elif t == "tool_use":
                        parts.append(f"[tool_use: {block.name} {block.input}]")
            lines.append(f"{role}: {' '.join(parts)}")

    return "\n\n".join(lines)
