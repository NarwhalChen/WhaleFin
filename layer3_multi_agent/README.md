# Layer 3 — 多 Agent 体系

## 这层解决什么问题

让不同任务交给不同角色的 agent，而不是一个 agent 什么都做。
角色隔离的价值：explore agent 只能读，不可能意外写坏文件；verify agent 只做验证，结论可信。

**加这层之前：** 主 agent 一个人读文件、写文件、验证，角色混乱，工具集无法限制。
**加这层之后：** 主 agent 决策和调度，子 agent 专注执行，每个角色只有它需要的工具。

## 原始 Spec Prompt

> 先修改 agent，agent 要支持 is_interactive 传参数，支持 chat client 的传入用于共用，支持 system prompt 的传入（默认 default sys）。此外无需太多改变。
>
> 然后是 tools，新增几个 agent tools，这个 agent description 跟其他 tools 一样要同步到 system prompt 让 Claude 选择调用的时候有 context，要有一个 map 来做 name: system_prompt, tools 的 hash。
>
> msg list 从主 agent 部分做 deepcopy 防止 agent 调用不知道 context。有了这些信息后 agent tool 会调用 run_loop under interactive=False，返回结果是 msg 的最后一条消息。

## Spec 之外补充的细节

**1. Agent 触发方式选了方式 B（工具触发），不是方式 A（硬编码）**

方式 A：代码决定什么时候派子 agent（if 判断）。
方式 B：Claude 自己决定，通过调用 `agent` 工具触发。

选 B 的原因：把决策权还给 Claude。Claude 看到 verify 返回 FAIL 会自己决定重试，方式 A 做不到这点。

**2. `AgentTool` 是普通工具，走同一套 pipeline**

对 tool_execution.py 来说，`AgentTool` 和 `FileReadTool` 没有区别：
```
validate → permission → call() → 返回 string
```
"特殊"只在 `call()` 内部：启动另一个 `run_loop`，而不是读文件。

**3. sub agent 模拟了一次 user message**

```python
sub_messages = copy.deepcopy(main_messages_ref)  # 继承主 agent 历史
sub_messages.append({"role": "user", "content": task})  # 构造任务，不调 input()
```

`imitate user` 是手动构造 user message，不是等真人输入。

**4. sub agent 结果和 normal tool 走同一条路回 AI**

```
normal tool → tool_result → AI
sub agent   → tool_result → AI  ← 完全一样，Claude 看不出区别
```

两条路在 tool_execution.py 汇合。

## 关键设计决策

### 一个 agent = system_prompt + tools + messages

```python
AGENT_CONFIGS = {
    "explore": {"system": "只读探索专家...", "tools": [file_read, glob]},
    "verify":  {"system": "验证专家，回复以 PASS/FAIL 开头...", "tools": [file_read]},
}
```

`client` 所有 agent 共用，`messages` 每次 deepcopy 传入，不存在 config 里。

### interactive=False 的唯一区别

```python
# interactive=True (主 agent)
if stop_reason == "end_turn":
    user_input = input("You: ")  # 等人

# interactive=False (子 agent)
if stop_reason == "end_turn":
    return full_response  # 直接返回，不等人
```

tool_use 的处理逻辑两者完全一样，子 agent 也会多轮循环调工具。

### VerifyAgent 的 PASS/FAIL 解析

system prompt 约束格式：`"你的回复必须以 PASS 或 FAIL 开头"`

父 agent 解析（如果需要程序判断）：
```python
passed = result.strip().upper().startswith("PASS")
```

LLM 输出不可信，必须在 prompt 层约束格式。

## 架构图

```
主 agent (interactive=True)
│
├── STOP → input() → 下一轮
│
└── tool_use
      ├── normal tool → tool_result → 主 AI
      └── agent tool
            ├── deepcopy main messages
            ├── append task as user message
            └── sub agent (interactive=False)
                  ├── tool_use → 执行工具 → 继续循环
                  └── end_turn → return result → tool_result → 主 AI
```

## 运行

```bash
python layer3_multi_agent/agent.py
```

## 冒烟测试

```
You: 帮我用 explore agent 看看当前目录有哪些 Python 文件
→ [Agent] 启动 explore agent...
→ Claude 调用 agent 工具，ExploreAgent 用 glob 搜索
→ 返回文件列表，主 agent 总结 ✅

You: 写一个文件 /tmp/hello.txt 内容是 hello，然后用 verify agent 确认它存在
→ 写文件（permission 弹窗）
→ [Agent] 启动 verify agent...
→ VerifyAgent 读文件，返回 PASS ✅
```
