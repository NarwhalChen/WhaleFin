"""
Tool 基类

设计决策: 为什么所有 bool 默认 False？
- Fail-closed 原则: 忘了配置 = 当作危险工具处理
- is_read_only=False → 默认假设会写 → 触发 permission check
- is_concurrency_safe=False → 默认串行 → 不会并发写同一个文件
- 反过来: 如果默认 True，忘了配置的 bash 工具会跳过权限检查
"""

from abc import ABC, abstractmethod


class Tool(ABC):
    name: str
    description: str

    # JSON Schema，直接传给 Anthropic API 的 tools 参数
    input_schema: dict

    # Fail-closed 默认值：不知道就按最严格处理
    is_read_only: bool = False          # False = 假设会写，触发 permission check
    is_destructive: bool = False        # False = 假设不破坏，但配合 is_read_only 判断
    is_concurrency_safe: bool = False   # False = 串行，避免并发写入竞争

    @abstractmethod
    async def call(self, input: dict) -> str:
        """执行工具。收 dict，返回字符串。异常在这里 catch，不往外抛。"""
        pass

    def to_api_format(self) -> dict:
        """转换为 Anthropic API 需要的 tools 格式"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def validate(self, input: dict) -> str | None:
        """
        检查 input 是否符合 input_schema 的 required 字段。
        返回 None 表示通过，返回 "ERROR: ..." 表示失败。
        """
        required = self.input_schema.get("properties", {})
        required_fields = self.input_schema.get("required", [])
        for field in required_fields:
            if field not in input:
                return f"ERROR: invalid input — '{field}' is required"
        return None
