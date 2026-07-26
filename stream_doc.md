# 流式传输

> 从深度代理运行和子代理执行中流式传输实时更新

> **提示**：对于新应用，我们推荐使用[事件流式传输](/oss/python/deepagents/event-streaming)——这是 Deep Agents v0.6 中引入的类型化投影 API。事件流式传输为每个投影（子代理、消息、工具调用、值）提供独立的迭代器，因此你可以独立消费它们，而不是在 `stream_mode` 块上进行分支。

Deep Agents 构建在 LangGraph 的流式基础设施之上，对子代理流提供了一流支持。当深度代理将工作委派给子代理时，你可以独立地从每个子代理流式传输更新——实时跟踪进度、LLM 令牌和工具调用。

深度代理流式传输可以实现的功能：

- [**流式子代理进度**](#子代理进度)——跟踪每个子代理并行执行的过程
- [**流式传输 LLM 令牌**](#llm-令牌)——从主代理和每个子代理流式传输令牌
- [**流式传输工具调用**](#工具调用)——查看子代理执行过程中的工具调用和结果
- [**流式传输自定义更新**](#自定义更新)——从子代理节点内部发出用户定义的信号

---

## 启用子图流式传输

Deep Agents 使用 LangGraph 的子图流式传输来呈现子代理执行的事件。要接收子代理事件，请在流式传输时启用 `stream_subgraphs`。

```python
from deepagents import create_deep_agent

agent = create_deep_agent(
    model="openai:gpt-5.5",
    system_prompt="你是一个有帮助的研究助理",
    subagents=[
        {
            "name": "researcher",
            "description": "深入研究某个主题",
            "system_prompt": "你是一个细致的研究员。",
        },
    ],
)

for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "研究量子计算进展"}]},
    stream_mode="updates",
    subgraphs=True,  # 启用子图流式传输
    version="v2",
):
    if chunk["type"] == "updates":
        if chunk["ns"]:
            # 子代理事件 - 命名空间标识来源
            print(f"[子代理: {chunk['ns']}]")
        else:
            # 主代理事件
            print("[主代理]")
        print(chunk["data"])
```

---

## 命名空间

当启用 `subgraphs` 时，每个流式事件都包含一个**命名空间**，用于标识产生该事件的代理。命名空间是节点名称和任务 ID 的路径，表示代理层次结构。

| 命名空间 | 来源 |
|---|---|
| `()` (空) | 主代理 |
| `("tools:abc123",)` | 由主代理的 `task` 工具调用 `abc123` 生成的子代理 |
| `("tools:abc123", "model_request:def456")` | 子代理内部的模型请求节点 |

使用命名空间将事件路由到正确的 UI 组件：

```python
for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "规划我的假期"}]},
    stream_mode="updates",
    subgraphs=True,
    version="v2",
):
    if chunk["type"] == "updates":
        # 检查此事件是否来自子代理
        is_subagent = any(
            segment.startswith("tools:") for segment in chunk["ns"]
        )

        if is_subagent:
            # 从命名空间提取工具调用 ID
            tool_call_id = next(
                s.split(":")[1] for s in chunk["ns"] if s.startswith("tools:")
            )
            print(f"子代理 {tool_call_id}: {chunk['data']}")
        else:
            print(f"主代理: {chunk['data']}")
```

---

## 子代理进度

使用 `stream_mode="updates"` 在每个步骤完成时跟踪子代理进度。这对于显示哪些子代理处于活动状态以及它们完成了哪些工作非常有用。

```python
from deepagents import create_deep_agent

agent = create_deep_agent(
    model="openai:gpt-5.5",
    system_prompt=(
        "你是一个没有研究知识的项目协调员。"
        "对于每个用户请求，你必须使用 task() 工具，"
        "并将 subagent_type 设置为 researcher。永远不要自己回答研究问题。"
        "将你的最终响应保持在一句话。"
    ),
    subagents=[
        {
            "name": "researcher",
            "description": "深入研究主题",
            "system_prompt": (
                "你是一个细致的研究员。研究给定的主题"
                "并提供 2-3 句话的简洁摘要。"
            ),
        },
    ],
)

for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "写一篇关于 AI 安全的简短摘要"}]},
    stream_mode="updates",
    subgraphs=True,
    version="v2",
):
    if chunk["type"] == "updates":
        # 主代理更新（空命名空间）
        if not chunk["ns"]:
            for node_name, data in chunk["data"].items():
                if node_name == "tools":
                    # 返回给主代理的子代理结果
                    for msg in data.get("messages", []):
                        if msg.type == "tool":
                            print(f"\n子代理完成: {msg.name}")
                            print(f"  结果: {str(msg.content)[:200]}...")
                else:
                    print(f"[主代理] 步骤: {node_name}")

        # 子代理更新（非空命名空间）
        else:
            for node_name, data in chunk["data"].items():
                print(f"  [{chunk['ns'][0]}] 步骤: {node_name}")
```

输出示例：
```
[主代理] 步骤: model_request
  [tools:call_abc123] 步骤: model_request
  [tools:call_abc123] 步骤: tools
  [tools:call_abc123] 步骤: model_request

子代理完成: task
  结果: ## AI 安全报告...
[主代理] 步骤: model_request
```

---

## LLM 令牌

使用 `stream_mode="messages"` 从主代理和子代理流式传输单个令牌。每个消息事件都包含标识来源代理的元数据。

```python
current_source = ""

for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "研究量子计算进展"}]},
    stream_mode="messages",
    subgraphs=True,
    version="v2",
):
    if chunk["type"] == "messages":
        token, metadata = chunk["data"]

        # 检查此事件是否来自子代理（命名空间包含 "tools:"）
        is_subagent = any(s.startswith("tools:") for s in chunk["ns"])

        if is_subagent:
            # 来自子代理的令牌
            subagent_ns = next(s for s in chunk["ns"] if s.startswith("tools:"))
            if subagent_ns != current_source:
                print(f"\n\n--- [子代理: {subagent_ns}] ---")
                current_source = subagent_ns
            if token.content:
                print(token.content, end="", flush=True)
        else:
            # 来自主代理的令牌
            if "main" != current_source:
                print("\n\n--- [主代理] ---")
                current_source = "main"
            if token.content:
                print(token.content, end="", flush=True)

print()
```

---

## 工具调用

当子代理使用工具时，你可以流式传输工具调用事件以显示每个子代理正在做什么。工具调用块出现在 `messages` 流模式中。

```python
from langchain.messages import AIMessageChunk, ToolMessage

for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "研究最近的量子计算进展"}]},
    stream_mode="messages",
    subgraphs=True,
    version="v2",
):
    if chunk["type"] == "messages":
        token, metadata = chunk["data"]

        # 识别来源："main" 或子代理命名空间段
        is_subagent = any(s.startswith("tools:") for s in chunk["ns"])
        source = next((s for s in chunk["ns"] if s.startswith("tools:")), "main") if is_subagent else "main"

        # 工具调用块（流式工具调用）
        if isinstance(token, AIMessageChunk) and token.tool_call_chunks:
            for tc in token.tool_call_chunks:
                if tc.get("name"):
                    print(f"\n[{source}] 工具调用: {tc['name']}")
                # 参数分块流式传输 - 增量写入
                if tc.get("args"):
                    print(tc["args"], end="", flush=True)

        # 工具结果
        if isinstance(token, ToolMessage):
            print(f"\n[{source}] 工具结果 [{token.name}]: {str(token.content)[:150]}")

        # 常规 AI 内容（跳过工具调用消息）
        if (
            isinstance(token, AIMessageChunk)
            and token.content
            and not token.tool_call_chunks
        ):
            print(token.content, end="", flush=True)

print()
```

---

## 自定义更新

在子代理工具内部使用 [`get_stream_writer`](https://reference.langchain.com/python/langgraph/config/get_stream_writer) 来发出自定义进度事件：

```python
import time
from langchain.tools import tool
from langgraph.config import get_stream_writer
from deepagents import create_deep_agent

@tool
def analyze_data(topic: str) -> str:
    """对给定主题运行数据分析。

    此工具执行实际分析并发出进度更新。
    对于任何分析请求，你必须调用此工具。
    """
    writer = get_stream_writer()

    writer({"status": "starting", "topic": topic, "progress": 0})
    time.sleep(0.5)

    writer({"status": "analyzing", "progress": 50})
    time.sleep(0.5)

    writer({"status": "complete", "progress": 100})
    return (
        f'对 "{topic}" 的分析：客户情绪 85% 为正面，'
        "由产品质量和支持响应时间驱动。"
    )

agent = create_deep_agent(
    model="openai:gpt-5.5",
    system_prompt=(
        "你是一个协调员。对于任何分析请求，你必须使用 task 工具"
        "委派给 analyst 子代理。永远不要试图直接回答。"
        "收到结果后，用一句话总结。"
    ),
    subagents=[
        {
            "name": "analyst",
            "description": "执行具有实时进度跟踪的数据分析",
            "system_prompt": (
                "你是一个数据分析师。对于每个分析请求，你必须调用 analyze_data 工具。"
                "不要使用任何其他工具。"
                "分析完成后，报告结果。"
            ),
            "tools": [analyze_data],
        },
    ],
)

custom_event_count = 0
for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "分析客户满意度趋势"}]},
    stream_mode="custom",
    subgraphs=True,
    version="v2",
):
    if chunk["type"] == "custom":
        custom_event_count += 1
        is_subagent = any(s.startswith("tools:") for s in chunk["ns"])
        if is_subagent:
            subagent_ns = next(s for s in chunk["ns"] if s.startswith("tools:"))
            print(f"[{subagent_ns}]", chunk["data"])
        else:
            print("[main]", chunk["data"])
```

输出示例：
```
[tools:call_abc123] {'status': 'starting', 'topic': 'customer satisfaction trends', 'progress': 0}
[tools:call_abc123] {'status': 'analyzing', 'progress': 50}
[tools:call_abc123] {'status': 'complete', 'progress': 100}
```

---

## 组合多种流模式

组合多种流模式以获得代理执行的完整视图：

```python
# 跳过内部中间件步骤 - 只显示有意义的节点名称
INTERESTING_NODES = {"model", "tools"}

last_source = ""
mid_line = False  # 当写入没有换行符的令牌时为 True

for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "分析远程工作对团队生产力的影响"}]},
    stream_mode=["updates", "messages", "custom"],
    subgraphs=True,
    version="v2",
):
    is_subagent = any(s.startswith("tools:") for s in chunk["ns"])
    source = "subagent" if is_subagent else "main"

    if chunk["type"] == "updates":
        for node_name in chunk["data"]:
            if node_name not in INTERESTING_NODES:
                continue
            if mid_line:
                print()
                mid_line = False
            print(f"[{source}] 步骤: {node_name}")

    elif chunk["type"] == "messages":
        token, metadata = chunk["data"]
        if token.content:
            # 当来源改变时打印头部
            if source != last_source:
                if mid_line:
                    print()
                    mid_line = False
                print(f"\n[{source}] ", end="")
                last_source = source
            print(token.content, end="", flush=True)
            mid_line = True

    elif chunk["type"] == "custom":
        if mid_line:
            print()
            mid_line = False
        print(f"[{source}] 自定义事件:", chunk["data"])

print()
```

---

## 常见模式

### 跟踪子代理生命周期

监控子代理何时启动、运行和完成：

```python
active_subagents = {}

for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "研究最新的 AI 安全发展"}]},
    stream_mode="updates",
    subgraphs=True,
    version="v2",
):
    if chunk["type"] == "updates":
        for node_name, data in chunk["data"].items():
            # ─── 阶段 1：检测子代理启动 ────────────────────────
            # 当主代理的 model 节点包含 task 工具调用时，
            # 子代理已被生成。
            if not chunk["ns"] and node_name == "model":
                for msg in data.get("messages", []):
                    for tc in getattr(msg, "tool_calls", []):
                        if tc["name"] == "task":
                            active_subagents[tc["id"]] = {
                                "type": tc["args"].get("subagent_type"),
                                "description": tc["args"].get("description", "")[:80],
                                "status": "pending",
                            }
                            print(
                                f'[生命周期] PENDING  → 子代理 "{tc["args"].get("subagent_type")}" '
                                f'({tc["id"]})'
                            )

            # ─── 阶段 2：检测子代理正在运行 ─────────────────────────
            # 当我们从 tools:UUID 命名空间收到事件时，该子代理正在执行。
            if chunk["ns"] and chunk["ns"][0].startswith("tools:"):
                pregel_id = chunk["ns"][0].split(":")[1]
                # 检查是否有待处理的子代理需要标记为正在运行。
                # 注意：pregel 任务 ID 与 tool_call_id 不同，
                # 因此我们在第一个子代理事件时将任何待处理的子代理标记为正在运行。
                for sub_id, sub in active_subagents.items():
                    if sub["status"] == "pending":
                        sub["status"] = "running"
                        print(
                            f'[生命周期] RUNNING  → 子代理 "{sub["type"]}" '
                            f"(pregel: {pregel_id})"
                        )
                        break

            # ─── 阶段 3：检测子代理完成 ──────────────────────
            # 当主代理的 tools 节点返回工具消息时，
            # 子代理已完成并返回了结果。
            if not chunk["ns"] and node_name == "tools":
                for msg in data.get("messages", []):
                    if msg.type == "tool":
                        sub = active_subagents.get(msg.tool_call_id)
                        if sub:
                            sub["status"] = "complete"
                            print(
                                f'[生命周期] COMPLETE → 子代理 "{sub["type"]}" '
                                f"({msg.tool_call_id})"
                            )
                            print(f"  结果预览: {str(msg.content)[:120]}...")

# 打印最终状态
print("\n--- 最终子代理状态 ---")
for sub_id, sub in active_subagents.items():
    print(f"  {sub['type']}: {sub['status']}")
```

---

## v2 流式格式

> **注意**：需要 LangGraph >= 1.1。

本页所有示例都使用 v2 流式格式（`version="v2"`），这是推荐的方式。每个块都是一个包含 `type`、`ns` 和 `data` 键的 `StreamPart` 字典——无论流模式、模式数量或子图设置如何，形状都相同。

v2 格式消除了嵌套元组解包，使得在 Deep Agents 中处理子图流式传输更加直接。比较两种格式：

**v2（推荐）——统一格式，无嵌套元组解包：**
```python
for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "研究量子计算"}]},
    stream_mode=["updates", "messages", "custom"],
    subgraphs=True,
    version="v2",
):
    print(chunk["type"])  # "updates"、"messages" 或 "custom"
    print(chunk["ns"])    # () 表示主代理，("tools:<id>",) 表示子代理
    print(chunk["data"])  # 有效载荷
```

**v1（旧版）——必须处理嵌套元组：**
```python
for namespace, chunk in agent.stream(
    {"messages": [{"role": "user", "content": "研究量子计算"}]},
    stream_mode=["updates", "messages", "custom"],
    subgraphs=True,
):
    mode, data = chunk[0], chunk[1]
    print(mode)       # "updates"、"messages" 或 "custom"
    print(namespace)  # () 表示主代理，("tools:<id>",) 表示子代理
    print(data)       # 有效载荷
```


# 事件流式传输

> 从 Deep Agents 流式传输子代理、消息、工具调用和最终输出。

本页涵盖 Deep Agents 特定的流式传输问题——最重要的是，通过 `stream.subagents` 从委派的子代理进行流式传输。关于通用代理流式传输（`stream.messages`、`stream.values`、工具调用、自定义更新），请参阅 [LangChain 事件流式传输](/oss/python/langchain/event-streaming)。

---

## 流式子代理

Deep Agents 在 LangGraph 流式传输之上添加了子代理投影。当你想要每个委派的 `task` 调用都有一个流句柄时，使用 `stream.subagents`。该投影是轻量级的：它首先发现子代理任务，并且消息、工具调用和值流只在你访问子代理句柄上的它们时才打开。

每个句柄的 `name` 是子代理的配置名称：协调器在调用 `task` 工具时传递的 `subagent_type`。Deep Agents 将该名称绑定到委派的运行，因此你在子代理规范中定义的相同标签就是你在流中过滤和路由所依据的标签。

```python
stream = agent.stream_events(
    {
        "messages": [{"role": "user", "content": "给我写一首关于大海的俳句"}],
    },
    version="v3",
)

subagent_names: list[str] = []
for subagent in stream.subagents:
    print(subagent.name, subagent.path, subagent.status)

    for message in subagent.messages:
        print(message.text)

    subagent_names.append(subagent.name)
```

---

## 子代理流字段

每个子代理流暴露与父运行相同类型的投影，例如消息、工具调用、嵌套子代理和最终输出。关于通用父运行流式传输模型，请参阅 [LangChain 事件流式传输](/oss/python/langchain/event-streaming)。

Python 使用蛇形命名法（snake_case）的投影名称，如 `tool_calls`。每个子代理流可以暴露 `.messages`、`.tool_calls`、`.values`、`.subagents` 和 `.output`。

| 字段 | 描述 |
|---|---|
| `name` | 子代理名称，取自协调器在其 `task` 调用中选择的 `subagent_type`。 |
| `messages` | 子代理发出的消息。 |
| `subagents` | 嵌套的子代理调用。 |
| `output` | 最终子代理状态，或委派任务的完成信号。 |
| `path` | 子代理流的命名空间路径。 |
| `status` | 生命周期状态，如 `started`、`completed`、`failed` 或 `interrupted`。 |
| `tool_calls` | 子代理范围内的工具调用。 |

---

## 跟踪子代理生命周期

当你只需要显示哪些子代理启动和完成时，使用 `stream.subagents`。除非你在单个子代理上访问这些投影，否则不需要订阅消息或值流。

```python
stream = agent.stream_events(input, version="v3")

running = 0
completed = 0
failed = 0

for subagent in stream.subagents:
    running += 1
    print(f"{subagent.name}: 已启动")

    try:
        _ = subagent.output
        running -= 1
        completed += 1
        print(f"{subagent.name}: 已完成")
    except Exception:
        running -= 1
        failed += 1
        print(f"{subagent.name}: 失败")
```

---

## 流式传输消息

Deep Agents 可以从协调器代理和委派的子代理发出消息。使用 `stream.messages` 获取顶层消息，使用 `subagent.messages` 获取每个委派的子代理的消息。

```python
stream = agent.stream_events(input, version="v3")

coordinator_messages: list[str] = []
for message in stream.messages:
    print("[协调器]", message.text)
    coordinator_messages.append(message.text)

for subagent in stream.subagents:
    for message in subagent.messages:
        print(f"[{subagent.name}]", message.text)
```

---

## 流式传输工具调用

Deep Agents 在代理树的每个层级暴露工具调用。使用顶层 `stream.tool_calls` 获取协调器工具，使用每个 `subagent.tool_calls` 获取委派的工作。

```python
stream = agent.stream_events(input, version="v3")

coordinator_tool_names: list[str] = []
for call in stream.tool_calls:
    print("[协调器工具]", call.tool_name, call.input)
    print(call.completed, call.error)
    coordinator_tool_names.append(call.tool_name)

for subagent in stream.subagents:
    for call in subagent.tool_calls:
        print(f"[{subagent.name} 工具]", call.tool_name, call.input)
        for delta in call.output_deltas:
            print(delta, end="", flush=True)

        if call.completed and call.error is None:
            print(call.output)
        elif call.error is not None:
            print(call.error)
```

---

## 流式传输嵌套工作

你可以递归进入子代理流以观察嵌套的子代理、消息和工具调用。

```python
stream = agent.stream_events(input, version="v3")

subagent_names: list[str] = []
for subagent in stream.subagents:
    print(f"子代理 {subagent.name}: {subagent.status}")

    for tool_call in subagent.tool_calls:
        print(f"{tool_call.tool_name}({tool_call.input})")
        for delta in tool_call.output_deltas:
            print(delta, end="", flush=True)

    for nested in subagent.subagents:
        print(f"嵌套子代理 {nested.name}: {nested.status}")

    subagent_names.append(subagent.name)
```

---

## 并发消费

协调器和子代理的输出通常是交错的。当你需要实时 UI 更新时，并发消费投影。

对于异步代码中的并发消费，使用 `astream_events` 配合 `asyncio.gather`：

```python
import asyncio

stream = await agent.astream_events(input, version="v3")

async def consume_coordinator():
    async for message in stream.messages:
        print("[协调器]", await message.text)

async def consume_subagents():
    async for subagent in stream.subagents:
        async for message in subagent.messages:
            print(f"[{subagent.name}]", await message.text)

await asyncio.gather(consume_coordinator(), consume_subagents())
```

对于同步代码，使用 `stream.interleave(...)` 代替：

```python
stream = agent.stream_events(input, version="v3")

for name, item in stream.interleave("messages", "subagents"):
    if name == "messages":
        print("[协调器]", item.text)
    else:
        for message in item.messages:
            print(f"[{item.name}]", message.text)
```

当你需要跨协调器和所有子代理的确切到达顺序时，迭代原始协议事件并使用 `namespace` 识别来源：

```python
stream = agent.stream_events(input, version="v3")

text_deltas: list[str] = []
for event in stream:
    if event.get("method") != "messages":
        continue

    payload = event["params"]["data"][0]
    if not isinstance(payload, dict):
        continue
    if payload.get("event") != "content-block-delta":
        continue

    block = payload.get("delta") or {}
    if block.get("type") == "text-delta":
        source = "子代理" if event["params"]["namespace"] else "协调器"
        print(f"[{source}] {block['text']}")
        text_deltas.append(block["text"])
```

---

## 子代理与子图

`stream.subgraphs` 显示图执行结构。`stream.subagents` 显示产品级的 Deep Agents 任务委派。对于面向用户的 UI，使用 `stream.subagents`，因为它隐藏了内部图节点并直接暴露子代理概念。

---



# 事件流式传输

> 从 LangChain 代理运行中流式传输实时更新

LangChain 代理构建在 LangGraph 之上，因此它们支持相同的流式传输栈，并提供面向代理的投影，用于消息、工具调用、状态和自定义更新。

对于大多数应用和前端用例，通过 `stream_events(..., version="v3")` 使用**事件流式传输**。事件流式传输返回一个带有类型化投影的运行对象，因此每个投影可以独立消费，而不需要解析流模式元组。

```python
from langchain.agents import create_agent

def get_weather(city: str) -> str:
    """获取城市天气。"""
    return f"{city} 的天气总是晴朗！"

agent = create_agent(
    model="gpt-5-nano",
    tools=[get_weather],
)

stream = agent.stream_events({
    "messages": [{"role": "user", "content": "旧金山的天气怎么样？"}],
}, version="v3")

for message in stream.messages:
    for delta in message.text:
        print(delta, end="", flush=True)

final_state = stream.output
```

---

## 你可以流式传输什么

| 投影 | 用途 |
|---|---|
| `for event in stream` | 原始协议事件，包含完整信封和每个通道的访问权限。 |
| `stream.messages` | 模型消息流，每个 LLM 调用一个。 |
| `message.text` | 消息的文本增量和最终文本。 |
| `message.reasoning` | 暴露推理内容的模型的推理增量。 |
| `message.tool_calls` | 工具调用参数块和最终确定的工具调用。 |
| `message.output` | 模型调用完成后的最终消息对象。 |
| `stream.values` | 代理状态快照。 |
| `stream.output` | 最终代理状态。 |
| `stream.subgraphs` | 嵌套图运行（子代理和普通子图）。 |
| `stream.extensions` | 自定义转换器投影。 |
| `stream.tool_calls` | 工具执行生命周期、输入、输出增量、最终输出和错误。 |

`stream.messages` 产生 `ChatModelStream` 对象。每个消息流暴露 `.text`、`.reasoning`、`.tool_calls` 和 `.output`。同步投影对于实时增量是可迭代的，对于最终值是可排空的：使用 `str(message.text)` 获取最终文本，使用 `message.tool_calls.get()` 获取最终确定的工具调用。

---

## 代理消息

当你想要每个 LLM 调用的模型输出时，使用 `stream.messages`。

```python
stream = agent.stream_events(input, version="v3")

for message in stream.messages:
    print(f"[{message.node}] ", end="")
    for delta in message.text:
        print(delta, end="", flush=True)

    full_message = message.output
    usage = full_message.usage_metadata
    if usage:
        print(usage)
```

`message.output` 给你最终确定的 AI 消息，包括提供商特定的内容块。在 TypeScript 中，当你只需要令牌计数或其他使用元数据时使用 `message.usage`；在 Python 中，从 `message.output.usage_metadata` 读取使用信息。

---

## 推理内容

推理内容与文本内容具有相同的形状，但仅当所选模型发出推理块时才可用。

```python
stream = agent.stream_events(input, version="v3")

for message in stream.messages:
    for delta in message.reasoning:
        print(f"[思考] {delta}", end="", flush=True)

    for delta in message.text:
        print(delta, end="", flush=True)
```

---

## 工具调用

有两个有用的工具调用投影：

- `message.tool_calls` 在模型生成工具调用时流式传输工具调用参数块。
- `stream.tool_calls` 在工具调用开始后流式传输工具执行的生命周期。

```python
stream = agent.stream_events(input, version="v3")

for message in stream.messages:
    for chunk in message.tool_calls:
        print(f"工具调用块: {chunk}")

    finalized = message.tool_calls.get()
    if finalized:
        print(f"最终确定的工具调用: {finalized}")

for call in stream.tool_calls:
    print(f"{call.tool_name}({call.input})")
    for delta in call.output_deltas:
        print(delta, end="", flush=True)
    print(call.output, call.error)
```

---

## 流式传输子代理

当一个 `create_agent` 调用调用另一个命名的 `create_agent`（通常通过包装工具）时，内部代理的事件在嵌套命名空间中流动。你传递给 `create_agent` 的 `name=` 在流中标识该内部代理，因此你可以按代理进行过滤和标记。

命名的子代理出现在专用的 `stream.subagents` 投影上。每个句柄暴露内部代理自己的 `.messages`、`.values`、`.tool_calls` 和 `.output`，加上 `.name`（你传递的 `name=`）和 `.cause`（分派子代理的工具调用）。因为只有命名的 `create_agent` 运行出现在这里，你不需要过滤掉普通的子图。

```python
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

def get_weather(city: str) -> str:
    """获取给定城市的天气。"""
    return f"{city} 的天气总是晴朗！"

weather_agent = create_agent(
    model=init_chat_model("openai:gpt-5.5"),
    tools=[get_weather],
    name="weather_agent",
)

def call_weather(query: str) -> str:
    """查询天气代理。"""
    result = weather_agent.invoke({"messages": [{"role": "user", "content": query}]})
    return result["messages"][-1].text

supervisor = create_agent(
    model=init_chat_model("openai:gpt-5.5"),
    tools=[call_weather],
    name="supervisor",
)

stream = supervisor.stream_events(
    {"messages": [{"role": "user", "content": "波士顿的天气怎么样？"}]},
    version="v3",
)

for subagent in stream.subagents:
    print(f"{subagent.name}: ", end="")
    for message in subagent.messages:
        for token in message.text:
            print(token, end="", flush=True)
    print()
```

从工具调用的普通 `StateGraph` 子图也会出现在 `stream.subgraphs` 上——在 `.compile(name=...)` 上设置 `name=` 可以在 `subagent.graph_name` 中获得标签。

`stream.subagents` 是命名 `create_agent` 子代理的聚焦视图，而 `stream.subgraphs` 覆盖每个嵌套图。根据你的 UI 使用合适的那个。

---

## 状态和最终输出

使用 `stream.values` 获取状态快照，使用 `stream.output` 获取最终代理状态。

```python
stream = agent.stream_events(input, version="v3")

for snapshot in stream.values:
    print(snapshot)

final_state = stream.output
```

---

## 多个投影

对于异步代码中的并发消费，使用 `astream_events` 配合 `asyncio.gather`：

```python
import asyncio

stream = await agent.astream_events(input, version="v3")

async def consume_messages():
    async for message in stream.messages:
        print(await message.text)

async def consume_tool_calls():
    async for call in stream.tool_calls:
        print(call.tool_name, call.input)

await asyncio.gather(consume_messages(), consume_tool_calls())
```

对于同步代码，使用 `stream.interleave(...)` 代替：

```python
stream = agent.stream_events(input, version="v3")

for name, item in stream.interleave("messages", "tool_calls", "values"):
    if name == "messages":
        print(item.text)
    elif name == "tool_calls":
        print(item.tool_name, item.input)
    elif name == "values":
        print(item)
```

要访问未作为类型化投影暴露的通道，或检查完整事件信封，请迭代原始协议事件：

```python
for event in stream:
    print(event["method"], event["params"]["namespace"], event["params"]["data"])
```

---

## 自定义更新

当你的应用需要内置投影之外的投影时，使用自定义流转换器，例如检索进度、工件或领域特定事件。

```python
stream = agent.stream_events(
    input,
    version="v3",
    transformers=[ToolActivityTransformer],
)

for activity in stream.extensions["tool_activity"]:
    print(activity)
```

### 在中间件上注册转换器

> **注意**：中间件注册的转换器需要 `langchain>=1.3.2`。

中间件可以在其钩子和工具旁边声明流转换器工厂。工厂形状在不同语言之间有所不同：

在 `AgentMiddleware` 子类上设置 `transformers` 属性为工厂序列。每个工厂的形状为 `Callable[[tuple[str, ...]], StreamTransformer]`，并作为 `factory(scope)` 调用，其中 `scope` 是迷你复用器范围元组（`()` 表示根复用器，非空表示子图）。每次调用返回一个全新的转换器，保持每个子图隔离。

```python
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware

class ToolActivityMiddleware(AgentMiddleware):
    transformers = (ToolActivityTransformer,)

agent = create_agent(
    model="gpt-5-nano",
    tools=[get_weather],
    middleware=[ToolActivityMiddleware()],
)
```

在编译时，`create_agent` 合并中间件注册的工厂与传递给其自身 `transformers=` 参数的任何内容。编译图上的最终顺序是：

1. 内置的 `ToolCallTransformer`
2. 中间件注册的工厂，按中间件顺序
3. 调用者从 `create_agent` 提供的 `transformers=`

这使内置的工具调用投影保持在消费者转换器之前，并给调用者提供的条目最终决定权。

内置的 `PIIMiddleware` 使用此钩子从流式传输的线路输出中编辑 PII。启用 `apply_to_output=True` 后，其注册的转换器在文本增量、工具调用参数、工具输出和状态快照离开运行之前，从中擦除检测到的 PII，关闭了 `after_model` 状态级编辑原本会让原始 PII 通过 `stream_events(version="v3")` 的实时读取器泄露的窗口。

```python
from langchain.agents import create_agent
from langchain.agents.middleware import PIIMiddleware

agent = create_agent(
    model="gpt-5-nano",
    tools=[],
    middleware=[
        PIIMiddleware("email", strategy="redact", apply_to_output=True),
    ],
)
```

---
