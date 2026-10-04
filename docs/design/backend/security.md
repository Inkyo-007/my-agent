# security 层设计说明

## 定位与职责

security 是安全防护层：工具执行前的权限检查。危险操作在此被拦截、询问或放行，以执行钩子（`tools/executor.py` 的 `ExecutionHook`）的形式接入执行流水线，tools 层不感知它的存在。

三个子层各自独立设计、独立落地，最后由**执行器集成**组装：

| 子层 | 文件 | 职责 | 状态 |
|---|---|---|---|
| 权限决策 | `permissions.py` | 裁决所需的决策要素：风险等级、权限模式、标签分类 | 已落地 |
| 路径校验 | `path_validator.py` | 判定调用涉及的路径在工作区内/外，防御路径穿越 | 已落地 |
| 命令护栏 | `guardrails.py` + `guardrails_rules.py` | 解析 shell 命令，硬阻断危险命令（rm -rf /、dd 等）；引擎与规则表分离 | 已落地 |
| 执行器集成 | `secure_executor.py` | 组装三个子层，实现最终裁决 `decide` 与审批通道，以执行钩子接入 tools 层 | 已落地 |

本层只依赖 core；组合与接线（构造钩子、注入审批通道）在 application 层。

**为什么 decide 不在 permissions.py**：完整的裁决需要路径内/外（path_validator）与命令分析（guardrails）的结果作为输入。三个子层各自完备，集成处只做一次组装（见 `secure_executor.py`），避免决策逻辑随子层落地反复改写。

## 权限决策（permissions.py）

只提供决策要素，不做最终裁决：

- **风险等级**（`RiskLevel`，静态，随工具走）：由工具的 `permission_required` 标签映射——`fs:read`→SAFE、`fs:write`→MUTATING、`shell:execute`→DANGEROUS。多标签取最高；未知标签兜底 DANGEROUS（fail-closed：我们不认识的操作按最高警惕处理——只读模式拒绝、ASK 模式必询问，宁可多询问，不可漏拦截）。空标签列表视为 SAFE——工具显式声明无任何权限需求（tools.md 约定声明要保守诚实，漏报等于绕过安全检查）。
- **权限模式**（`PermissionMode`，动态，用户会话级选择）：只读（`READ_ONLY`）、请求询问（`ASK`）、完全访问（`FULL_ACCESS`）。
- **网络域判定**（`is_network_tag`）：`net:` 域标签一律视为「使用网络」触发器。

与权限标签的「领域」保持开放不同，风险等级是封闭的安全语义（不生长），因此用枚举；领域新增时无需改动本文件，未知标签由兜底逻辑处理。

## 路径校验（path_validator.py）

纯分类，不做策略裁决：按 input_schema 中 `format == "path"` 的标注提取调用中的路径参数，逐个判定为 `INSIDE` / `OUTSIDE` / `INVALID`，输出 `PathReport`；「OUTSIDE 该怎么办」由执行器集成结合权限模式决定。

关键设计：

- **与读写工具的分工**：执行器集成落地后，fs 工具内曾经的路径硬围栏已移除——读写工具只接收路径并忠实执行，内/外由本层分析，工作区外经审批放行。`shell_command` 同理：工具自身不设护栏，危险命令与界外 cwd 都在执行前裁决。
- **解析机制内聚在本层**：「规范化 + 判内外」（`resolve_in_root`）只有 security 需要，按 core 章程（针对某一层的内容不下沉）留在本层；将来若有他层需要，再收敛到 core。判定经 NFC 归一化、符号链接展开、`is_relative_to` 比较，不用字符串前缀比较。
- **fail-closed**：无法解析的路径判 `INVALID`，`PathReport.touches_outside` 将其按界外同等对待。
- **不吸收参考实现中的 URL 迭代解码**：我们的路径来自模型工具调用的 JSON（已解码一次），链路上没有第二次 URL 解析；主动解码反而会把无害字符串变成穿越路径。

**已知边界**（触发任一条件时需升级方案）：Windows 8.3 短名不展开（`PROGRA~1` 可绕过等长前缀判断，利用难度大）；硬链接无法按路径分辨（同一文件多路径）；判定与执行之间的 TOCTOU 竞态（判定后符号链接被替换，真正解决需 OS 级手段）。

## 命令护栏（guardrails.py）

### 职责边界

纯分析，不做策略裁决：解析 shell 命令，输出 `CommandReport`；「blocked 怎么办」「flagged 要不要问」由执行器集成结合权限模式决定。护栏独有的、权限矩阵给不了的语义只有一个：**硬阻断**——`rm -rf /`、`Format-Volume` 这类没有合法场景的命令，在任何模式（含完全访问）下都必须拒绝。权限模式管「要不要问」，护栏管「绝不执行」。

shell 工具的方言策略：**Git Bash 主方言，pwsh 降级**（探测顺序 bash → pwsh → powershell，探测与注入在 application 层）。Git Bash 是行业主流选择（Claude Code、Codex 同），模型熟练度最高；pwsh 护栏能力 v1 收窄（见「已知边界」），但 `shell:execute` 是 DANGEROUS，审批是真正的闸门，护栏是第二道防线。

### 数据模型

- `ShellDialect`：`BASH` / `PWSH`；
- `RuleVerdict`：`BLOCK`（硬阻断，任何模式拒绝）/ `FLAG`（标注：写进审批提示与审计，不改变裁决——`shell:execute` 本就必询问，FLAG 服务于审批提示质量，如「该命令包含 `rm -rf`，目标在工作区外」）；
- `CommandRule`（frozen dataclass）：`name`、`pattern`（token 前缀，首词已规范化）、`verdict`、`reason`（用户可读，进审批提示 / 喂回模型）、`dialects`、`match` / `not_match`（应命中/不应命中示例，借鉴 Codex execpolicy 的规则自测，在 pytest 中逐条校验，防规则表腐烂）；
- `CommandReport`：`blocked`（任一子命令命中 BLOCK）、`flagged`（命中明细，供审批提示指名）、`referenced_paths`（cd / 重定向目标的原始字符串，交 path_validator 分类——单一职责，护栏不做路径判定）、`unparsable`（解析失败，fail-closed，集成层不得自动放行）；
- 入口：`analyze(command: str, dialect: ShellDialect) -> CommandReport`。

### 解析管线（四步）

1. **拆链**：自写引号感知的小型扫描器（尊重单双引号与转义），bash 按 `&&` `||` `;` `|` `|&` `&` 换行拆分，pwsh 按 `;` `|` `&&` `||` 拆分；每段再 `shlex.split` 分词，失败置 `unparsable`。不用 `shlex.punctuation_chars`（Python 3.12+，项目兼容 3.10）。**误报的根源是不尊重引号，不是缺 AST**——参考实现用 `operator in command` 子串匹配导致 `echo "a && b"` 被拦，本设计从拆链起就避开这一类。
2. **argv[0] 规范化**：小写、去路径前缀（`/bin/rm`、`C:\Windows\...`、`./rm` → `rm`）、去 `.exe`；pwsh 查别名表（`rm`/`del`→`Remove-Item`、`ls`/`dir`/`gci`→`Get-ChildItem`、`type`/`cat`/`gc`→`Get-Content`、`curl`/`iwr`→`Invoke-WebRequest`、`kill`→`Stop-Process` 等，借鉴 Claude Code 的别名规范化）。
3. **规则匹配**：token 前缀匹配（`pattern=("git","push","--force")` 匹配 `git push --force origin main`）；**任一子命令命中 BLOCK 即整链 blocked**（借鉴「规则须独立覆盖每条子命令」）。BLOCK 表：`rm -rf /|/*|~`、`mkfs`、`dd of=/dev/`、`shutdown`/`Stop-Computer`、`Format-Volume`、`format` 等；FLAG 表：`rm -rf`、`git push --force`、`git reset --hard`、`curl|wget` 管道、`Remove-Item -Recurse` 等。
4. **路径提取**：`cd` 的目标与重定向目标（`>` `>>` `2>` `<` 的下一 token）收入 `referenced_paths`——堵住 `cd C:\ && del ...` 与 `echo x > C:\important` 两类围栏后门。

### 递归分析与未分析结构

- shell 系的内联执行（`bash -c` / `sh -c` / `pwsh -Command`）的参数**本身就是一条命令，递归 `analyze()`**（限深 3 层），而不是一禁了之；
- 无法递归的解释器内联执行（`python -c`、`node -e` 等）FLAG「内联解释器执行，内容不可分析」；
- 命令含 `$(`、反引号、`if` / `for` / `while` 时不假装分析过，FLAG「包含命令替换/控制流，请人工确认」——fail-visible（审批提示展示全文），而非 fail-silent。

### 已知边界

- **挡不住**：`bash -c` 之外的变形（变量展开、eval、`xargs`、base64 等）、pwsh 的 scriptblock / `&` 调用运算符 / .NET 反射。护栏是纵深防御的一环，不是沙箱——审批与未来的 OS 级沙箱（参考 Codex 的 windows-sandbox-rs：受限 token、ACL、WFP 防火墙）才是真正的边界。文档附「挡得住 / 挡不住」对照语料（借鉴 Claude Code 的诚实写法）；
- **bashlex 升级路径**：bash AST 解析器（Python 库）可正确分析嵌套替换与控制流，当前不引入（新依赖收益不足、pwsh 无对等方案）；触发条件：需要分析嵌套结构语义时。

## 裁决矩阵（decide 的规格）

最终裁决 = **风险等级** × **权限模式**，必要时由**上下文触发器**升级。触发器有两个：工作区外写入（运行期事实，由 paths 子层提供）、使用网络（由标签推导）。

| 条件 | 只读 | 请求询问 | 完全访问 |
|---|---|---|---|
| SAFE | 放行 | 放行 | 放行 |
| MUTATING（无触发器） | 拒绝 | 放行 | 放行 |
| DANGEROUS（无触发器） | 拒绝 | 询问 | 放行 |
| 触发器命中（工作区外写入 / 网络） | 按风险等级正常分派 | **始终询问** | 放行 |

两个刻意的取舍：

- **只读模式下网络读取放行**：只读模式的语义是「不改本地状态」，网络读取不改本地。
- **命令硬阻断不进裁决矩阵**：`rm -rf /` 这类没有合法场景的命令由 commands 子层在任何模式（含完全访问）下直接拒绝——权限模式管「要不要问」，护栏管「绝不执行」。

decide 的输出是 core 的 `HookVerdict` 三态裁决（`ALLOW` / `DENY` / `ASK_USER`）。执行器集成只做决策与组装，**不执行审批**：`ASK_USER` 的解决（如何询问用户、等待回答）由审批通道承担，集成处在钩子内部把裁决收敛为终态后再交给执行器。

## 执行器集成（secure_executor.py）

以执行钩子（`tools/executor.py` 的 `ExecutionHook`）接入执行流水线，组装三个子层完成一次调用的完整裁决：

1. **命令护栏先行**：对 `format == "command"` 标注的参数运行 `analyze()`，命中 BLOCK 规则直接 `DENY`——硬阻断在任何模式（含完全访问）下生效，不走审批；
2. **路径校验**：汇总两类路径——schema 中 `format == "path"` 标注的参数值，与护栏从命令中提取的 `referenced_paths`（cd 目标、重定向目标）——保序去重后经 `PathValidator` 判定，`touches_outside` 作为裁决触发器；
3. **裁决矩阵**：`decide(mode, risk, triggers)` 输出三态；命令 `unparsable` 在完全访问模式下升级为 `ASK_USER`（分析不了的命令不给静默放行）；
4. **审批通道**：`ASK_USER` 经 `Approver` 协议（`async def approve(request: ApprovalRequest) -> bool`）解决，请求携带工具名、理由、**工具输入原文**（要执行的命令、要写入的路径——审批必须指名道姓，用户才能做知情决定）、界外路径与护栏标注，供 UI 展示；审批经 `asyncio.Lock` 串行化，避免并发弹窗。未注入审批通道时 fail-closed 按拒绝处理。

钩子返回前发布审计事件：每次调用的裁决（`PERMISSION_CHECK`）、审批请求（`APPROVAL_REQUEST`）、硬阻断（`DENIAL_REQUEST`），source 均为 `secure_executor`。审批通道的具体实现（CLI 询问、Web 弹窗）在 application 层注入，本层不感知。

## 已知边界

- **审批记忆未实现**：每次询问相互独立，「本次会话不再询问」「始终允许该工具」类规则持久化留待后续（v1 的 Approver 回答只有放行/拒绝二值）；
- **CLI 审批通道已接入**：application 层的 `CliApprover`（终端 y/N）是 Approver 的首个实现；Web 弹窗随前端落地后作为另一个实现注入。

## 测试方式

纯函数离线测试（`backend/test/security/`）：权限决策覆盖风险分类与网络域判定；命令护栏覆盖规则表 match/not_match 自测、拆链与分词（引号/转义/方言差异）、argv[0] 规范化与 pwsh 别名、硬阻断与标注、shell -c 递归与限深、路径提取、unparsable 兜底（`test_guardrails.py`）；路径校验覆盖内/外/非法分类、fail-closed 约定、format 提取与参数缺失的跳过（`test_path_validator.py`），解析机制另有穿越、符号链接逃逸等攻击语料（`test_paths.py`，符号链接用例在 Windows 无权限时自动降级跳过）。执行器集成覆盖裁决矩阵、三模式流水线、硬阻断不走审批、unparsable 升级、审批通道的放行/拒绝/未注入/串行化与事件序列（`test_secure_executor.py`）。三个子层无打桩需求（不接触 IO）；执行器集成的审批通道与事件总线用内存假实现。
