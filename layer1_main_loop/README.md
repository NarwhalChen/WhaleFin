# Layer 1 — 主循环状态机

## 这层解决什么问题

让 Claude 能持续对话。没有这层，每次调用 API 都是独立的，Claude 不记得上一句说了什么。

**加这层之前：** 每次 API 调用都是一次性的，无状态。
**加这层之后：** 有 `while True` 主循环，messages list 积累整个对话历史，Claude 有上下文记忆。

## 原始 Spec Prompt

> 完成 main，首先初始化 chat client，此外 init message list，tool list，然后传入 run loop。
>
> run_loop：在得到 message 后将 message（注意区分 role）存入 list，跟 access token 一起使用来调用 Claude API。Claude 将返回一个 streaming，边解析边展示，在 streaming 结束后按照 role（用户是 user，AI 是 assistant）存入 messages。

## Spec 之外补充的细节

写 spec 时不知道、但实现时必须处理的东西：

**1. 入口点必须是 `asyncio.run(main())`**

```python
if __name__ == "__main__":
    asyncio.run(main())
```

不能只写 `async def main()` 就结束——async 函数不会自己跑，必须交给 event loop。

**2. 用 `AsyncAnthropic` 不是 `Anthropic`**

```python
client = AsyncAnthropic()  # 正确
client = Anthropic()       # 错误：在 async def 里用同步客户端不报错，只是默默阻塞事件循环
```

**3. System prompt 是单独的参数，不放进 messages**

```python
client.messages.stream(
    system="You are a helpful assistant.",  # 单独传
    messages=messages,                       # 不包含 system
)
```

**4. `run_loop` 签名从 Layer 1 开始统一**

```python
async def run_loop(messages: list, tools: list = [], client: AsyncAnthropic = None) -> None:
```

Layer 1 的 `tools` 永远是空列表，但签名要和 Layer 2-5 一致，
这样上层才能 `from layer1_main_loop.agent import run_loop as base_loop`。

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

递归：Python 默认 recursion limit 1000。100 轮对话 × 5 次工具调用 = 500 次递归，接近极限。
而且状态藏在调用栈里，出问题只能看 stack trace。

while True：状态显式存在 messages list 里，随时可以打印、检查、修改。
Layer 4 的 token 计数也是因为这个才容易实现。

### 为什么从 Layer 1 就用 AsyncAnthropic？

Layer 3 的 background agent 需要 `asyncio.create_task`，只能在 event loop 里调用。
如果 Layer 1 用同步客户端，到 Layer 3 就必须把整个主循环推倒重来。

代价：多写 `async def`、`await`、`asyncio.run(main())`。
收益：Layer 3 不需要重构，架构一致到底。

## 运行

```bash
python layer1_main_loop/agent.py
```

## 冒烟测试

发三条消息，确认 Claude 记得第一条说了什么：

```
You: 我叫小明
Assistant: 你好小明！...

You: 你知道我叫什么吗
Assistant: 你叫小明！...   ← messages list 正常积累，多轮记忆有效 ✅
```
