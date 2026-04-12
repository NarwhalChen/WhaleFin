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

**2. stop_reason 决定流程**

```python
if final.stop_reason == "tool_use":
    # 执行工具，直接进下一轮，不等用户输入
elif final.stop_reason == "max_tokens":
    # 输出被截断，注入续写信号，对用户透明
elif final.stop_reason == "end_turn":
    # 等用户输入
```

**3. orderlist 防止结果乱序**

`asyncio.gather` 结果顺序和输入一致，但 safe/unsafe 分组后直接拼接会乱序。
解法：预分配 `results = [None] * n`，按原始 index 写回，不拼接。

**4. 工具定义直接传 API，不需要改 system prompt**

```python
client.messages.stream(
    tools=[t.to_api_format() for t in tools],  # 直接传，API 原生支持
)
```

**5. StreamingToolExecutor：边收边跑**

传统做法等模型全部输出完再执行工具。Layer 2 改为监听原始事件流：

```
safe block 完整  → asyncio.create_task 立刻开跑
unsafe block 完整 → 攒起来
message_stop     → gather 等 safe 结束 → unsafe 串行跑
```

用原始事件流（`async for event in stream`）而不是 `text_stream`，
才能在 `content_block_stop` 事件触发时立刻拿到完整的 tool_use block。

## 关键设计决策

### Fail-closed 默认值

```python
is_read_only: bool = False         # 默认假设会写 → 触发 permission check
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

### StreamingToolExecutor 的三种方案与 tradeoff

实现边收边跑时有三种选择：

**A — 所有 block 完整立刻开跑（忽略 safe/unsafe）**
延迟最低，但违反 `is_concurrency_safe` 语义。两个 `file_write` 同时跑可能损坏文件。

**B — 攒齐所有 block 再跑（原版行为）**
安全但没有 streaming 收益，加了事件监听却没用上。

**C — safe 立刻开跑，unsafe 攒起来等 safe 结束再串行（最终选择）**
safe 工具拿到 streaming 收益，unsafe 工具保持安全语义，和 `is_concurrency_safe` 字段的设计意图一致。

边缘情况：safe 和 unsafe 之间存在逻辑依赖时（先 write 再 read），方案 C 无法感知，可能读到旧内容。这是已知 tradeoff，实际场景中 Claude 通常不会在同一个 turn 里混用有依赖的 safe/unsafe 工具。


## 运行

```bash
python layer2_tool_system/agent.py
```

## 冒烟测试

```
You: 读一下 requirements.txt
→ [Tools] 执行 1 个工具调用（streaming 模式）...（无 permission 弹窗，is_read_only=True）
→ anthropic==0.40.0 ✅

You: 在 /tmp/test.txt 里写入 hello world
→ [Permission] 工具: file_write
               参数: {'path': '/tmp/test.txt', 'content': 'hello world'}
               允许执行? [y/N] y
→ 写入成功 ✅

You: 同时读 requirements.txt 和搜索所有 py 文件
→ [Tools] 执行 2 个工具调用（streaming 模式）...
→ file_read 和 glob 同时开跑（safe），模型还在输出时已完成 ✅

You: 读一下 /tmp/不存在的文件.txt
→ FileNotFoundError: /tmp/不存在的文件.txt not found
→ loop 不崩，继续对话 ✅
```
