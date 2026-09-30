# application — 组合根

## 定位与职责

application 是应用的组装入口：读取配置、创建各层实例并互相连接。它是**唯一了解全局接线的地方**——其余功能层只依赖 core 端口，彼此互不认识（依赖方向见 docs/rules/backend.md）。

职责边界同样有两条硬规则：

- 只做「创建与连接」，不包任何业务逻辑。会话管理、权限策略、压缩策略等都长在对应功能层，application 只把它们接进来；
- 新增模块的合法改动是「新增文件 + 在入口处接线」，而不是改动既有模块的内部逻辑。

## 文件一览

| 文件 | 内容 |
|---|---|
| `config.py` | 环境变量（.env）→ `ModelConfig`，附缺失/非法变量的明确报错 |
| `app.py` | `create_app()` 组合根 + `Application` 门面（只暴露 `loop` 与 `bus`） |
| `__main__.py` | 最小 CLI 冒烟入口（`poe chat`），开发期手动验证用 |

## 关键设计

**配置与接线拆成两个文件**。config.py 只负责「读环境、出配置」，不认识任何运行时对象；app.py 只负责接线，不关心配置从哪来。两者可独立测试：配置用 monkeypatch 环境变量即可，接线测试注入脚本化 Provider，互不牵扯。

**`provider` 参数是离线测试的接缝**。`create_app()` 默认按 config 创建真实 Provider，同时接受注入——离线集成测试借此把 `ScriptedProvider` 接进完整接线，在离线状态下验证跨层契约（tool_call 配对、事件序列、历史完整性）。这也是对接线本身的测试：真实 bus / registry / executor / loop / convert 全部走真代码，只有模型边界被替换。

**`Application` 门面只暴露 `loop` 和 `bus`**。外部消费者（将来的 Web UI）需要的就是这两样：通过 `loop.run()` 驱动任务，通过 `bus.subscribe()` 接收事件。历史挂在 `loop.history` 上，不另开入口。门面越薄，后续层落地时的接线改动越集中。

**事件是 CLI 的输出来源**。`__main__.py` 不直接读循环内部状态，而是订阅总线打印事件——与将来 UI 的接入方式同构，冒烟的同时也在验证事件流对订阅者是否够用。

## 使用方式

```bash
cp backend/.env.example backend/.env   # 填入 API 密钥与模型
poe chat                                # 终端冒烟对话
```

代码内接线：

```python
from src.application import create_app, load_model_config

app = create_app(load_model_config())   # tools=None：内置工具待 tools/builtin 落地
app.bus.subscribe(my_handler)
result = await app.loop.run("你好")
```

## 已知边界

- **无预装工具**：tools 层的 `builtin/` 尚未落地（M2），`create_app(tools=...)` 是预留的接入口。当前 CLI 只能跑纯问答；工具调用链路已由离线集成测试覆盖。
- **无会话持久化**：进程退出即丢失历史，由 memory 层解决；落地后 application 负责把恢复的 `MessageHistory` 接进 `AgentLoop`。
- **无流式输出**：与 runtime 层一致，待 UI 接入前补充。
- **配置项刻意最少**：只覆盖协议类型、密钥、模型、base_url。思考档位、max_tokens 等调参项等真实需求出现时再加（届时只动 config.py）。

## 测试方式

测试位于 `backend/test/application/`：

- `test_load_config.py`：monkeypatch 环境变量验证加载逻辑；autouse fixture 阻断真实 `.env` 加载，避免本机配置污染「变量缺失」用例。
- `test_app.py`：离线集成测试——除 Provider 注入 `ScriptedProvider` 外全部走真实实现，断言跨层拼缝：tool_call 配对与结果回喂、loop 与 executor 共享总线的事件序列、幻觉工具调用的失败回喂、模型异常的 FAILED 收尾。
- `test_e2e_live.py`：真实 API 端到端冒烟（`-m live`，消耗额度，手动运行）：纯问答闭环与工具调用闭环各一例。