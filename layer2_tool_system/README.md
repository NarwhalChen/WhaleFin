# Layer 2 — 工具系统

## 这层解决什么问题

让 Claude 能操作真实世界：读文件、写文件、执行命令、搜索路径。
没有工具，Claude 只能说话，不能做事。

**加这层之前：** Claude 只能返回文字，无法读写文件或执行命令。
**加这层之后：** Claude 可以调用工具，代码真正运行在你的机器上。

## 原始 Spec Prompt

> 支持 streaming 结束后对 tool call 的 parse，支持两种情况：end 则进入 user input，tool_use 则不进 user input，摘出 tool call 的 {toolname, tool_args} 并进行 tools 的 append 存储。然后将 tool dicts 传入 run_tools。
>
> run_tools 首先 check tools 基类查看是否存在，这个 tools 基类要有以下参数：input_schema: dict，is_read_only: False（fail-closed，默认假设会写），is_destructive: False，is_concurrency_safe: False（默认串行）以及 tools 的 describe。
>
> run_tools 需要支持按照是否 concurrency_safe 分组：unsafe 就串行 pipeline，safe 就是并行 pipeline。首先是 validation check，检查当前 args 下 tools 能不能使用，如果不行就 fallback；如果 ok 则进行 permission check，如果没有 permission 就连同 tool call 信息展示到用户界面来获取权限。
>
> 注意在两个组实际运行之前先有个 orderlist 存储传入顺序，这样返回后可以按顺序存储并返回。fallback 部分在 invalid、不允许、exec 失败等情况返回 error 字段而不是抛出异常，防止阻断 loop。

## Spec 之外补充的细节

写 spec 时不知道、但实现时必须处理的东西：

**1. tool_use 之后 messages 要存两条**

```python
# 第一条：Claude 的工具调用请求（必须原样保留）
messages.append({"role": "assistant", "content": final.content})

# 第二条：工具执行结果
messages.append({"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": block.id, "content": result}
]})
```

只存一条不会报错，但 Claude 下一轮会困惑，因为看不到自己说过"我要用工具"。

**2. stop_reason 决定流程，不是"有没有 tool block"**

```python
if final.stop_reason == "tool_use":
    # 执行工具，不等用户输入，直接进下一轮
elif final.stop_reason == "end_turn":
    # 等用户输入
```

**3. orderlist 防止结果乱序**

`asyncio.gather` 的结果顺序和输入一致，但 safe/unsafe 分组后合并会乱序。
解法：预分配 `results = [None] * n`，按原始 index 写回，不拼接。

## 关键设计决策

### Fail-closed 默认值

```python
is_read_only: bool = False       # 默认假设会写 → 触发 permission check
is_concurrency_safe: bool = False  # 默认串行 → 不会并发写同一文件
```

忘了配置 = 当作危险工具处理。反过来如果默认 True，
忘了配置的 bash 工具会跳过权限检查，`rm -rf` 就绕过去了。

### 哪些工具可以是 concurrency_safe=True

| 工具 | is_concurrency_safe | 原因 |
|------|---------------------|------|
| file_read | True | 只读，多个并发读互不影响 |
| glob | True | 只查找，无副作用 |
| file_write | False | 并发写同一文件会竞争 |
| bash | False | 命令之间可能有依赖 |

### validate → permission → execute 的顺序

先 validate 再问权限：如果参数本来就是错的，不需要弹窗打扰用户。
所有错误统一返回 `"ERROR: ..."` 字符串，不抛异常，主循环不会崩。

## 运行

```bash
python layer2_tool_system/agent.py
```

冒烟测试：
1. `读一下 requirements.txt` → 直接读，不弹 permission 窗口（is_read_only=True）
2. `在 /tmp/test.txt 里写入 hello world` → 弹窗，选 y，文件写入
3. `读一下 /tmp/不存在的文件.txt` → 返回 ERROR，loop 不崩
