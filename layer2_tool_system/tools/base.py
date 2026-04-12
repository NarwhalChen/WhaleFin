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
        检查 input 是否符合 input_schema。
        1. required 字段必须存在
        2. 存在的字段类型必须匹配 JSON Schema type
        返回 None 表示通过，返回 "ERROR: ..." 表示失败。
        """
        properties = self.input_schema.get("properties", {})
        required_fields = self.input_schema.get("required", [])

        # 1. required 字段存在性检查
        for field in required_fields:
            if field not in input:
                return f"ERROR: invalid input — '{field}' is required"

        # 2. 类型检查
        _JSON_TYPE_MAP = {
            "string":  str,
            "number":  (int, float),
            "integer": int,
            "boolean": bool,
            "array":   list,
            "object":  dict,
        }
        for field, value in input.items():
            schema = properties.get(field, {})
            expected = schema.get("type")
            if expected is None:
                continue
            py_type = _JSON_TYPE_MAP.get(expected)
            if py_type and not isinstance(value, py_type):
                actual = type(value).__name__
                return f"ERROR: invalid input — '{field}' must be {expected}, got {actual}"

        return None
