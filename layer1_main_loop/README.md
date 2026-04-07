# Layer 1 — 主循环状态机

## 这层解决什么问题

让 Claude 能持续对话。没有这层，每次调用 API 都是独立的，Claude 不记得上一句说了什么。

**加这层之前：** 每次 API 调用都是一次性的，无状态。
**加这层之后：** 有 `while True` 主循环，messages list 积累整个对话历史，Claude 有上下文记忆。

## 原始 Spec Prompt

> 完成 main，首先初始化 chat client，此外 init message list，tool list，然后传入 run loop。
>
> run_loop: 在得到 message 后将 message（注意区分 role）存入 list，跟 access token 一起使用来调用 Claude API。Claude 将返回一个 streaming，边解析边展示，在 streaming 结束后按照 role（用户是 user，AI 是 assistant）存入 messages。

## 关键设计决策

### 为什么是 while True，不是递归？

```python
# 递归版本
async def agent_turn(messages):
    response = await claude(messages)
    return await agent_turn(messages + [response])  # 每轮一层栈帧

# while True 版本
async def run_loop(messages):
    while True:
        response = await claude(messages)
        messages.append(response)
```

递归：Python 默认 recursion limit 1000。100 轮对话 × 5 次工具调用 = 500 次递归，
接近极限。而且状态藏在调用栈里，出问题只能看 stack trace。

while True：状态就是变量，随时可以打印和检查。Layer 4 的 token 计数能轻松实现
也是因为这个——每轮都能访问完整的 messages list。

### 为什么从 Layer 1 就用 AsyncAnthropic？

Layer 3 的 background agent 需要 `asyncio.create_task`，只能在 event loop 里调用。
如果 Layer 1 用同步客户端，到 Layer 3 就必须把整个主循环推倒重来。

现在的代价：多写 `async def`、`await`、`asyncio.run(main())`。
换来的：Layer 3 不需要重构，架构一致。

### run_loop 的统一签名

```python
async def run_loop(messages: list, tools: list = [], client: AsyncAnthropic = None) -> None:
```

Layer 1 的 `tools` 永远是空列表，但签名要和 Layer 2-5 一致，
这样 Layer 2 才能 `from layer1_main_loop.agent import run_loop as base_loop`。

## 运行

```bash
python layer1_main_loop/agent.py
```

冒烟测试：发三条消息，确认 Claude 记得第一条说了什么。
