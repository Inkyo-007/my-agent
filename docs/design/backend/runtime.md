# runtime — 运行时引擎层

## 定位与职责

runtime 负责驱动 Agent 的主循环：组装上下文、调用模型、处理工具调用、记录历史，直到任务完成。它是把 models、tools 两层零件组装成「能跑的 Agent」的地方。此外，它还提供两项运行期基础设施：事件总线的内存实现，以及会话历史的存取。

职责边界：

- **不做协议翻译**：模型的字段差异、思考模式、流式碎片都已在 models 层被吸收，runtime 只面对 `ProviderMessage` / `ProviderResponse` 这套统一格式。
- **不做工具执行**：工具的存在性校验、参数校验、超时、权限钩子都在 tools 层的执行器里，runtime 只把 `ToolCall` 交给它并接收 `ToolResult`。
- **不做权限策略**：审批与拦截由 security 层以执行钩子的形式挂在执行器上，runtime 对此无感知——被拒绝的工具调用对循环来说只是一个普通的失败结果。
- **不做持久化**：会话历史只存在于内存。重启恢复、上下文压缩是 memory 层的事，runtime 只需保证历史的存取接口干净（见「历史与转换分离」）。
- **不做编排**：多 Agent、任务分解调度是 orchestration 层的事，runtime 只管单个 Agent 的一次任务。

## 依赖方向

runtime 只依赖 core 中定义的端口，不认识任何具体实现：

| 需要的能力 | 依赖的端口 | 具体实现（由 application 接线） |
|---|---|---|
| 调用模型 | `core.BaseProvider` | models 层的 `OpenAIProvider` / `ClaudeProvider` |
| 执行工具 | `core.BaseToolExecutor` | tools 层的 `ToolExecutor` |
| 广播事件 | `core.EventBus` | 本层的 `InMemoryEventBus` |

这意味着 runtime 的测试可以完全离线：用脚本化的假 Provider 和现成的测试工具即可跑通完整循环，不联网、不消耗额度。

## 文件一览

| 文件 | 内容 |
|---|---|
| `loop.py` | `AgentLoop`：主循环编排，`run()` 入口与 `RunResult` 结果类型 |
| `history.py` | `MessageHistory`：会话历史（`Message` 列表）的追加与读取 |
| `convert.py` | `Message` ↔ `ProviderMessage` 双向转换（纯函数） |
| `bus.py` | `InMemoryEventBus`：事件总线的内存实现 |
| `__init__.py` | 包导出 |

## 关键设计

### 主循环的语义

`AgentLoop.run(user_input)` 的一次执行过程：

1. 用户输入包装为 `Message` 追加进历史，发射 `TASK_START`；
2. 进入循环：将历史转换为 `ProviderMessage` 列表，连同工具清单一起交给 `provider.complete()`；
3. 模型响应转为 assistant `Message` 入历史。若响应不含工具调用，任务正常结束（`TASK_END`）；
4. 若含工具调用，逐个交给 `executor.execute()`，每个结果转为 tool `Message` 入历史，然后回到第 2 步；
5. 达到 `max_steps` 仍未自然结束，则以 `max_steps_reached` 状态收尾——这是兜底，不是错误；
6. Provider 抛异常（网络故障、鉴权失败等）时，循环包装为失败的 `RunResult` 并发射 `TASK_FAILED`，**不向调用方抛异常**。

「一轮」（step）定义为一次模型调用及其引发的全部工具执行，每轮发射 `STEP_START` / `STEP_END`（失败时 `STEP_FAILED`）。`TOOL_*` 系列事件由执行器发射，循环不重复发。

两个值得说明的决策：

- **循环永不抛异常**。这与执行器「失败也是结果」的设计一脉相承，但理由不同：执行器不抛异常是因为失败要喂回模型自我纠正；而 Provider 失败时模型已不可达，无从喂回，此时不抛异常是为了让调用方（application/UI）能以统一的方式渲染成功与失败，不必在每一层都套 try。
- **工具调用串行执行**。同一轮的多个 tool_call 按顺序逐个执行。并行执行（`asyncio.gather`）留到真实需求出现——它带来错误聚合、事件交错、权限审批顺序等一系列复杂度，第一版不值得。

`run()` 返回 `RunResult`（runtime 层自定义的 dataclass）：状态枚举（`completed` / `max_steps_reached` / `failed`）、步数、累计 token 用量、失败描述。不使用 core/agent.py 的 `ExecutionResult`——它是无使用方的早期占位（`status` 为裸字符串），其去留待 application 层落地时一并定夺，runtime 不依赖它。

### 协议字段随消息元数据走

主循环里最微妙的问题是：历史用应用层的 `Message` 记录，喂模型时需要协议层的 `ProviderMessage`，而后者携带 `Message` 没有的字段（思考签名、加密思考数据、tool_call 配对）。这些字段放在哪？

方案：**全部放进 `Message.metadata`，且只放 JSON 可序列化的原始结构**。具体约定：

- assistant 消息：`thinking_signature`、`redacted_thinking`、`tool_calls`（`list[dict]`，每项含 `id`/`name`/`input`）；
- tool 消息：`tool_call_id`、`tool_name`、`status`（沿用 `ToolResultMessage` 既有约定）。

理由有三：

1. **单一事实来源**。不同时维护一份 `Message` 历史和一份 `ProviderMessage` 历史——两份平行历史必然在长会话中漂移，而漂移的 bug 极难排查。
2. **与既有模式一致**。`ToolCallMessage` 已经把工具名、参数放在 metadata 里用属性访问器暴露，协议字段沿用同一做法，消息结构保持稳定。
3. **为持久化铺路**。`to_dict()`/`from_dict()` 已保证 metadata 序列化往返不丢，memory 层落盘后即可完整重建协议状态。这也是「只放原始结构」的原因：`ToolCall` 对象不能直接 JSON 序列化，存字典、由转换层重建。

### 转换层独立成文件

双消息模型的转换是集成 bug 的高发区（tool_call 配对错位、思考签名丢失后 Claude 报 400），因此集中在 `convert.py` 的纯函数中，便于参数化测试穷举边界：

- `message_from_response(response) -> Message`：模型响应 → 历史消息；
- `message_from_tool_result(tool_call, result) -> ToolResultMessage`：工具结果 → 历史消息；
- `to_provider_messages(messages) -> list[ProviderMessage]`：历史 → 喂模型的格式。

**有损容忍策略**：缺思考签名时静默降级——不回传 thinking block（Anthropic 对无签名的 thinking block 会报错），并在消息 metadata 打上标记供审计。不抛异常：降级后模型只是失去思考上下文，仍能工作；抛异常会打断整个循环，代价不对等。

### 历史与转换分离

`MessageHistory` 只管「存什么、怎么追加」（`append()`、`messages` 只读视图、`to_provider_messages()` 委托 convert），convert 只管「喂给模型时长什么样」。分开的原因：将来的 memory 层做持久化只碰 history，上下文压缩只改 convert 的出口，两者互不干扰。system prompt 作为首条 `SYSTEM` 角色消息存入历史——`ClaudeProvider` 已经会把它剥离为顶层参数，历史无需特殊处理。

### 与 memory 层的分工与协作

一个自然的疑问是：上下文内容管理不是 memory 层的职责吗，为什么 runtime 里有 history？

分工的界线是：**history.py 是循环的贴身工作状态，memory 层是历史的管理策略**。主循环运转时必须有一个可追加、可读取的会话状态载体（模型响应与工具结果要逐条追加，下一轮调用前要读取），这个载体的职责被刻意压到最小：追加与只读视图。而依赖规则决定了它只能长在 runtime——runtime 不允许依赖 memory，放 core 又不满足「被两层以上使用」的准入标准。memory 层做的是 history 不做的事：持久化与重启恢复、上下文压缩与摘要、长期检索。一个是「历史是什么」，一个是「对历史做什么」。

两层不直接通信，协作经 application 接线与 core 的共享数据类型（`Message`）完成，三个方向各有一个挂载点：

- **持久化（runtime → memory）**：任务结束后由 application 读取 `loop.history.messages` 交给 memory 落盘（`to_dict()` 序列化已就绪）；或让 memory 以 EventBus 订阅者身份感知任务进展——总线是 core 端口，双方互不认识。
- **恢复（memory → runtime）**：application 从 memory 读出历史消息，构造 `AgentLoop` 时作为初始历史传入。runtime 只看到一组 `Message`，不感知它们来自磁盘。
- **压缩（memory 的策略注入 runtime 的流程）**：压缩本质是「喂模型前改造消息列表」，挂法是依赖注入——`AgentLoop` 接受一个可选的上下文变换 callable（`list[Message] -> list[Message]`），application 把 memory 的压缩策略接进来；不注入则原样透传（第一版的行为）。

将来若 memory 落地后认为 `MessageHistory` 应完全归 memory 管理，可将其上提为 core 端口、由 memory 实现——history 的接口刻意保持极简，就是为了让那次搬迁不痛苦。

### 事件总线的内存实现

`InMemoryEventBus` 实现 core 的 `EventBus` 契约：同步投递，逐个调用订阅者。契约要求「单个订阅者异常不影响其他订阅者与发射方」，实现上捕获订阅者异常、记录到标准 `logging` 后继续投递，异常不向外抛——事件是通知，订阅者的故障不能反过来打垮发射方。

第一版不做异步投递、过滤、批处理：订阅者是普通函数，过滤由订阅者自行处理（契约已约定）。这些能力等 observability 等真实订阅者出现后再长。

## 使用方式

runtime 不在内部创建任何依赖，全部实例由 application 组合根接线：

```python
bus = InMemoryEventBus()
bus.subscribe(my_ui_handler)          # 订阅者按类型自行过滤

registry = ToolRegistry()
registry.register(ReadFileTool())
executor = ToolExecutor(registry, hooks=[...], event_bus=bus)

provider = create_provider(config)    # models 层工厂
loop = AgentLoop(
    provider=provider,
    executor=executor,
    system_prompt="你是一个助手",
    tools=registry.list_tools(),
    max_steps=10,
    event_bus=bus,                     # 可选，不传则静默
)

result = await loop.run("帮我读一下 README.md 并总结")
# result.status: RunStatus.COMPLETED / MAX_STEPS_REACHED / FAILED
# loop.history.messages: 完整会话历史
```

## 已知边界

- **只做非流式**：第一版循环只调用 `complete()`。流式输出（`stream()` + 增量事件）是对外的产品能力，待 UI 接入前作为循环的增量能力补充；先用非流式把闭环的集成 bug（tool_call 配对、签名回传）暴露在最简单的形态下。
- **无上下文压缩**：长会话会持续累积直到顶到模型上下文上限，由 memory 层解决。
- **无中断/暂停**：`AgentState.PAUSED` 等状态尚无载体，取消机制待真实需求。
- **max_steps 收尾无总结**：模型持续调用工具直到触顶时，最后一轮的工具结果已入历史但没有模型的收尾表述，`RunResult.status` 会标记 `max_steps_reached`，如何向用户呈现由调用方决定。
- **无 checkpoint**：不实现 `save_checkpoint` / `restore_checkpoint`。对话回退的真实价值依赖持久化（memory 层）与 UI 入口，且真正的难点不在消息历史的快照（`to_dict`/`from_dict` 往返已天然支持），而在工具副作用的回滚——文件已被改写、命令已被执行，只回退对话会造成「对话回到过去、工作区留在未来」的不一致（Claude Code 为此配套了 shadow git，属需要单独立项的大设计）。触发引入的条件：memory 层落地 + UI 出现回退入口；届时 checkpoint 是「history 快照 + 存储」的增量加入，快照的存储管理归 memory 层，runtime 只保证可快照性，本层无需为此预留接口。

## 测试方式

测试位于 `backend/test/runtime/`，全程离线：

- **ScriptedProvider**（加入 `test/fakes.py`）：`BaseProvider` 的脚本化实现，按预设的 `ProviderResponse` 队列依次返回，并记录每次收到的 `ProviderMessage` 列表——既可以驱动多轮循环，也能断言「第 N 次调用模型时喂进去的上下文长什么样」。
- **CollectingBus**：从 `test/tools/test_executor.py` 提取到 `test/fakes.py` 共享（原位置改为导入），断言事件序列与元数据。
- 工具侧直接用现成的 `WeatherTool` / `StubTool`。

核心用例：

1. 无工具的单轮问答：一次模型调用即结束，断言消息序列与 `TASK_START`/`STEP_START`/`STEP_END`/`TASK_END` 事件序列；
2. 一轮工具调用闭环：模型发起 tool_call → 执行 → 结果回喂 → 模型给出最终回答，断言回喂的 `ProviderMessage` 内容（tool_call 配对、`tool_call_id`）；
3. 多轮工具调用与同一轮多个 tool_call（验证连续 tool_result 的生成，Claude 的合并规则依赖于此）；
4. `max_steps` 兜底：脚本 Provider 永远返回 tool_call，断言以 `max_steps_reached` 收尾；
5. Provider 抛异常：断言返回 failed `RunResult`、发射 `TASK_FAILED`、异常不向外抛；
6. 不传总线时完全静默；
7. 转换层的参数化单测：思考签名缺失的降级、metadata 序列化往返后再转换的一致性。
