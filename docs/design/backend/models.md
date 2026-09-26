# models — 模型调用层

## 定位与职责

models 层负责与各家大模型 API 通信：把统一的内部消息格式翻译成各家 API 的
请求格式，把各家的响应解析回统一格式。所有协议细节（字段名差异、思考模式、
工具调用的序列化方式）都封装在本层，上层（运行时等）只看到统一的接口。

最重要的划分原则：**一个实现对应一种 API 协议，而不是一个厂商**。

- `OpenAIProvider` 对应 OpenAI Chat Completions 协议，覆盖所有兼容该协议的
  服务（OpenAI 官方、DeepSeek、Qwen、Ollama 等）——换一个兼容服务只需改
  `base_url` 和 `model_id`，不需要新的 Provider 类；
- `ClaudeProvider` 对应 Anthropic Messages 协议。

兼容服务之间协议同构，剩下的差异（参数名、扩展字段）由配置吸收，而不是靠
复制 Provider 类堆出来。

## 文件一览

| 文件 | 内容 |
|---|---|
| `config.py` | `ModelConfig`：构造 Provider 所需的全部配置 |
| `openai_provider.py` | OpenAI Chat Completions 协议实现 |
| `claude_provider.py` | Anthropic Messages 协议实现 |
| `__init__.py` | `create_provider` 工厂：按配置创建对应的 Provider |

## 配置模型（ModelConfig）

`ModelConfig` 是「构造期」配置：由应用入口组装好传给 Provider，上层编排逻辑
不感知它。几个不太直观的字段值得说明：

| 字段 | 说明 |
|---|---|
| `max_tokens_param` | 长度限制参数名。OpenAI 官方新模型用 `max_completion_tokens`（o 系列强制），DeepSeek 等兼容服务仍用 `max_tokens` 且会**静默忽略**不认识的参数。「碰巧能跑」比直接报错更难排查，所以参数名显式配置，不靠猜 |
| `reasoning_effort` | 推理强度档位。非 None 即表示「开启思考」，各家映射到不同参数（见下节） |
| `thinking_budget` | Anthropic 手动预算模式的思考 token 数（本地校验：≥1024 且 < max_tokens） |
| `extra_body` | 厂商扩展参数原样透传（如 DeepSeek 的思考开关）。这是留好的「逃生门」：不在代码里为某个厂商写 if-else，新参数直接配置传入 |

`client_kwargs()` 只返回客户端构造参数（密钥、地址、超时、重试）的白名单，
请求级参数（模型、长度限制、思考配置）由 Provider 在每次调用时自行组装。

## 思考模式的统一语义

两家厂商的思考模式差异很大，本层把它们收敛成一条规则：
**`reasoning_effort` 非 None 即「开启思考」**。具体映射：

- OpenAI 兼容服务 → 传 `reasoning_effort` 参数；或经 `extra_body` 传厂商特有
  开关（DeepSeek）；
- Anthropic → `thinking_budget` 非 None 时走手动预算模式
  （`thinking={"type":"enabled","budget_tokens":N}`）；否则 `reasoning_effort`
  非 None 时走自适应模式（`thinking={"type":"adaptive"}` + 强度档位）。
  两者同时设置时，手动预算优先。

Anthropic 侧的自适应模式是**显式声明**的，不依赖「只传强度参数时 API 是否
默认开思考」这类未经证实的行为。

## 统一格式契约

两个 Provider 对外的输出格式完全一致，差异在内部消化：

- `ProviderResponse.tool_calls` 中，`ToolCall.input` 永远是解析好的字典
  （OpenAI 的 arguments 是 JSON 字符串，本层负责 `json.loads` 并容错；
  Anthropic 原生就是字典）；
- 流式的 `delta` 字典键名两家一致（工具调用增量统一为
  `{"index","id","name","input"}`）；
- `token_used` 语义一致：Anthropic 没有总 token 数，本层负责把
  input + output 相加。

## 流式输出的关键约定：STOP 最后发

两家厂商返回 token 统计的位置不同：DeepSeek 把它挂在结束块上，OpenAI 官方
则在结束块之后再单独发一个空块。如果在收到结束块时就发出 STOP，OpenAI 官方
形态下统计永远是 0。

因此统一约定：**流式过程中只暂存结束原因，循环结束后才发 STOP**，保证 STOP
携带完整统计、且一定是流的最后一个事件。这条不变量写进了 core 的契约
文档字符串，两个 Provider 都必须遵守。

## 已知边界（使用前先了解）

- **Anthropic 侧未经真实 API 验证**：目前全部测试是打桩的离线测试，待有
  API key 后需补真实契约测试（重点：思考回传、工具调用闭环、adaptive 实际
  行为）；
- **模型版本兼容**：手动预算模式在 Opus 4.7+ 已被移除（传了会报错）；
  adaptive 模式是 Opus 4.6 引入的，更老的模型不支持；
- **协议保真的取舍**：协议层消息用扁平字段（单份思考文本 + 单份签名），
  在当前模型与用法下够用；但启用 Anthropic 的 interleaved-thinking beta
  （多段思考交错）时会有损，届时需要升级为保留完整块序列的方案。

## 如何接入一个新的模型服务

1. 它是 OpenAI 兼容协议？直接用 `OpenAIProvider`，配置里换 `base_url` +
   `model_id` 即可，差异参数走 `extra_body`；
2. 它是一种全新协议（如 Gemini 原生协议）？新增一个 Provider 类实现
   `BaseProvider`，在 `create_provider` 工厂里加一行分发。

## 测试方式

- **离线打桩为主**：打桩点设在 SDK 调用边界（`client.*.create`），只验证
  「请求组装」与「响应解析」两层逻辑，不联网、不消耗额度。桩对象即对真实
  API 响应结构的声明——生产代码多读一个字段，桩必须跟着声明。
- **真实契约测试为辅**：标记 `@pytest.mark.live`，默认不运行，需手动执行
  `poe test-live`，用于验证对厂商行为的关键假设（字段名、回传规则）。
