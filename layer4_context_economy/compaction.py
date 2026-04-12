"""
轻量压缩：Snip + Micro Compact

执行顺序（每轮 tool result 写入前）：
  Snip   → 超长 result 存磁盘，messages 里只留 preview
  Micro  → deque(maxlen=3) 滑动窗口，被挤出的 id 对应 result 替换成占位符
"""

import os
from collections import deque
from pathlib import Path

SNIP_THRESHOLD = 30_000   # 超过这个字符数就存磁盘
SNIP_PREVIEW   = 2_000    # messages 里保留的 preview 长度
MICRO_MIN_LEN  = 120      # 短结果不 compact，保留原样


def snip_result(tool_use_id: str, result: str) -> str:
    """
    Snip Compact: 超长 result 存磁盘，返回 preview 摘要。
    不超长则原样返回。
    """
    if len(result) <= SNIP_THRESHOLD:
        return result

    out_dir = Path(".task_outputs/tool-results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{tool_use_id}.txt"
    out_file.write_text(result, encoding="utf-8")

    preview = result[:SNIP_PREVIEW]
    return (
        f"<persisted-output>\n"
        f"Full output saved to: {out_file}\n"
        f"Preview:\n{preview}\n"
        f"</persisted-output>"
    )


class MicroCompactor:
    """
    Micro Compact: 滑动窗口，只保留最近 3 条 tool result 的完整内容。

    每次新 tool_use_id 进来时：
    - 如果窗口已满（size == 3），最老的 id 被挤出
    - 在 messages 里找到被挤出 id 对应的 tool_result，替换成占位符
    - 短结果（<= MICRO_MIN_LEN）不替换，保留原样
    """

    PLACEHOLDER = "[Earlier tool result compacted. Re-run the tool if you need full detail.]"

    def __init__(self):
        self._window: deque[str] = deque(maxlen=3)

    def add(self, tool_use_id: str, messages: list) -> None:
        """注册新 tool_use_id，如果窗口满了就 compact 最老的那条。"""
        if len(self._window) == 3:
            old_id = self._window[0]   # 即将被挤出的最老 id
            self._compact_id(old_id, messages)
        self._window.append(tool_use_id)

    def _compact_id(self, tool_use_id: str, messages: list) -> None:
        """在 messages 里找到 tool_use_id 对应的 tool_result，原地替换。"""
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") == tool_use_id
                ):
                    current = block.get("content", "")
                    if isinstance(current, str) and len(current) > MICRO_MIN_LEN:
                        block["content"] = self.PLACEHOLDER
                    return
