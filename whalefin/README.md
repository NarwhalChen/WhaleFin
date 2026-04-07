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
