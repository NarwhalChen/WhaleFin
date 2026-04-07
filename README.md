# WhaleFin

从零构建 Claude Code Agent — 教学驱动实现

以 Xiao Tan 的《Claude Code 源码架构深度解析 V2.0》为蓝图，用 Python + Anthropic SDK
逐层实现一个简化版 coding agent，理解 Agent 系统工程的核心概念。

## 快速开始

```bash
pip install anthropic==0.40.0
cp .env.example .env  # 填入你的 API key
```

`.env` 格式：
```
ANTHROPIC_API_KEY=sk-ant-xxx
```

## 层级结构

| Layer | 目录 | 核心概念 |
|-------|------|----------|
| 1 | `layer1_main_loop/` | while True 状态机、streaming、多轮对话 |
| 2 | `layer2_tool_system/` | Tool 基类、validate→permission→execute pipeline、并发分组 |
| 3 | `layer3_multi_agent/` | ExploreAgent、VerifyAgent、fork/built-in/background |
| 4 | `layer4_context_economy/` | snip + auto + budget 三道压缩、token 计数 |
| 5 | `layer5_safety/` | bash_classifier、PreToolUse hooks、permission |

每层独立可运行：

```bash
python layer1_main_loop/agent.py
python layer2_tool_system/agent.py
```

## 原始 Spec Prompts

这个项目用 spec coding 方式构建：先用自然语言写规格，再由 AI 实现。
以下是每层的原始 spec prompt，记录"在理解之后能写出什么"。

### Layer 1 — 主循环

> 完成 main，首先初始化 chat client，此外 init message list，tool list，然后传入 run loop。
>
> run_loop: 首先在得到 message 后判断 list 是否为空来放入 system prompt（不过这个可以放在 main init），之后将 message（注意区分 role）存入 list，跟 access token 一起使用来调用 Claude API。Claude 将返回一个 streaming，边解析边展示，在 streaming 结束后按照 role：用户就是 user，AI 就是 assistant，content 的内容存入 messages。

### Layer 2 — 工具系统

> 接下来需要支持 streaming 结束后对 tool call 的 parse，支持两种情况：end 则进入 user input，tool_use 则不进 user input，摘出 tool call 的 {toolname, tool_args} 并进行 tools 的 append 存储。然后将 tool dicts 传入 run_tools。
>
> run_tools 首先 check tools 基类查看是否存在，这个 tools 基类要有以下参数：input_schema: dict，is_read_only: False（fail-closed，默认假设会写），is_destructive: False，is_concurrency_safe: False（默认串行）以及 tools 的 describe。
>
> run_tools 需要支持按照是否 concurrency_safe 分组：unsafe 就串行 pipeline 进行 tool call，safe 就是并行 pipeline。首先是 validation check，检查当前 args 下 tools 能不能使用，如果不行就 fallback；如果 ok 则进行 permission check，如果没有 permission 就连同 tool call 信息展示到用户界面来获取权限，如果 yes 则继续进入 tool 运行。
>
> 注意在两个组实际运行之前先有个 orderlist 存储传入顺序，这样返回后可以按顺序存储并返回。fallback 部分需要在 invalid、不允许、exec 失败等情况返回 error 字段而不是抛出异常，防止阻断 loop。
>
> tool_use 之后 messages 存两条：`{"role": "assistant", "content": final.content}` + `{"role": "user", "content": [tool_result blocks]}`。

## 设计原则

**为什么 while True 不用递归？**
Python recursion limit 1000，长对话会爆栈。while True 让状态显式存在 messages list 里。

**为什么从 Layer 1 就用 AsyncAnthropic？**
Layer 3 的 background agent 需要 asyncio.create_task，只能在 event loop 里调用。
同步主循环没有 event loop，到 Layer 3 就必须推倒重来。

**为什么 Tool 的 bool 字段全部默认 False？**
Fail-closed：忘了配置 = 当作危险工具处理。
is_read_only=False → 触发 permission check。
is_concurrency_safe=False → 串行执行，避免并发写入竞争。
