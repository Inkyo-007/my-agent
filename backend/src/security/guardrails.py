"""security 层 · 命令护栏子层：shell 命令的危险分析

职责边界：纯分析，不做策略裁决。解析 shell 命令输出 CommandReport；
「blocked / flagged 怎么处置」由执行器集成结合权限模式决定。护栏独有
语义只有硬阻断（BLOCK）：没有合法场景的命令在任何模式（含完全访问）
下都必须拒绝。

解析管线（设计与理由见 docs/design/backend/security.md）：
拆链（引号感知）→ argv[0] 规范化 → 逐子命令规则匹配 → 路径提取；
shell 系 -c 参数递归分析（限深 _MAX_DEPTH 层），未分析结构如实 FLAG
（fail-visible），不假装分析过。

文件拆分：数据模型（ShellDialect / RuleVerdict / CommandRule）与规则表
（RULES）在 guardrails_rules.py；本文件是报告模型与分析引擎。

已知边界：挡不住变量展开、eval、xargs、base64 与 pwsh scriptblock 等
变形；bash 方言下模型应使用 POSIX 路径（shlex 会把 Windows 反斜杠当
转义符处理）。护栏是纵深防御的一环，不是沙箱。
"""

import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from .guardrails_rules import RULES, RuleVerdict, ShellDialect

# 递归分析 shell -c 参数的层级上限（防嵌套滥用）
_MAX_DEPTH = 3


@dataclass(frozen=True)
class FlaggedCommand:
    """一次规则命中：子命令原文 + 命中规则与理由"""

    subcommand: str
    rule_name: str
    verdict: RuleVerdict
    reason: str


@dataclass
class CommandReport:
    """一次命令分析的结果

    referenced_paths 是原始字符串（cd / 重定向目标），内/外分类交
    path_validator；unparsable=True 表示有子命令无法解析（fail-closed，
    集成层不得自动放行）。
    """

    blocked: bool = False
    flagged: List[FlaggedCommand] = field(default_factory=list)
    referenced_paths: List[str] = field(default_factory=list)
    unparsable: bool = False


_BASH = ShellDialect.BASH
_PWSH = ShellDialect.PWSH
_BLOCK = RuleVerdict.BLOCK
_FLAG = RuleVerdict.FLAG

# 链式运算符（长运算符优先匹配）；pwsh 的 & 是调用运算符而非分隔符
_CHAIN_OPERATORS: Dict[ShellDialect, Tuple[str, ...]] = {
    _BASH: ("&&", "||", "|&", "&", "|", ";", "\n"),
    _PWSH: ("&&", "||", "|", ";", "\n"),
}

# pwsh 别名 → 规范化 cmdlet 名（小写），借鉴 Claude Code 的别名规范化
_PWSH_ALIASES: Dict[str, str] = {
    "rm": "remove-item",
    "del": "remove-item",
    "erase": "remove-item",
    "rd": "remove-item",
    "rmdir": "remove-item",
    "ri": "remove-item",
    "ls": "get-childitem",
    "dir": "get-childitem",
    "gci": "get-childitem",
    "type": "get-content",
    "cat": "get-content",
    "gc": "get-content",
    "cp": "copy-item",
    "copy": "copy-item",
    "mv": "move-item",
    "move": "move-item",
    "curl": "invoke-webrequest",
    "wget": "invoke-webrequest",
    "iwr": "invoke-webrequest",
    "kill": "stop-process",
    "sleep": "start-sleep",
    "cd": "set-location",
    "chdir": "set-location",
    "echo": "write-output",
}

# shell 系内联执行：-c 参数本身是一条命令，递归 analyze（program → 内层方言与标志位）
_RECURSIVE_SHELLS: Dict[str, Tuple[ShellDialect, Set[str]]] = {
    "bash": (_BASH, {"-c"}),
    "sh": (_BASH, {"-c"}),
    "zsh": (_BASH, {"-c"}),
    "pwsh": (_PWSH, {"-c", "-command"}),
    "powershell": (_PWSH, {"-c", "-command"}),
}

# 不可递归的解释器内联执行：内容无法分析，FLAG 如实暴露
_INLINE_INTERPRETERS: Dict[str, Set[str]] = {
    "python": {"-c"},
    "python3": {"-c"},
    "py": {"-c"},
    "node": {"-e", "--eval"},
    "perl": {"-e"},
    "ruby": {"-e"},
}

# 控制流关键字：循环/分支体内的命令未完全分析
_CONTROL_FLOW_KEYWORDS: Dict[ShellDialect, Set[str]] = {
    _BASH: {"if", "for", "while", "until", "case", "select"},
    _PWSH: {"if", "for", "foreach", "while", "switch", "try"},
}

_CD_PROGRAMS = {"cd", "chdir", "pushd", "set-location"}
_CD_FLAGS = {"-", "-P", "-L"}
_REDIRECT_RE = re.compile(r"^\d*(>>?|<)(.*)$")
_REDIRECT_IGNORE = {
    "/dev/null",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/stdin",
    "nul",
    "nul:",
}


def analyze(command: str, dialect: ShellDialect) -> CommandReport:
    """分析一条 shell 命令，输出危险分析报告（纯函数，无副作用）

    命令护栏的唯一入口。分析是保守的：只报告能确定的事实（命中的
    规则、引用的路径、解析失败），不下「安全」结论——未被标记不等于
    安全（已知边界见模块 docstring）。

    Args:
        command: 待分析的命令全文（可含链式运算符与多条子命令）。
        dialect: shell 方言，决定拆链运算符、分词规则与 argv[0] 别名表。

    Returns:
        CommandReport：blocked（是否命中硬阻断）、flagged（命中明细，
        供审批提示指名）、referenced_paths（cd / 重定向目标，内/外分类
        交 path_validator）、unparsable（是否有子命令解析失败，
        fail-closed，集成层不得自动放行）。

    示例:
        analyze("rm -rf /", BASH).blocked               → True（硬阻断）
        analyze("ls && mkfs /dev/sda", BASH).blocked    → True（任一子命令命中即整链阻断）
        analyze("rm -rf ./build", BASH).blocked         → False（工作区内删除只标注）
    """
    return _analyze(command, dialect, depth=0)


def _analyze(command: str, dialect: ShellDialect, depth: int) -> CommandReport:
    """analyze 的递归工作函数：整体扫描 + 逐子命令处理

    处理顺序：深度检查 → 未分析结构标注（整串一次）→ 逐段（子命令）
    处理。每个子命令依次经过：分词（失败置 unparsable 并跳过）→
    argv[0] 规范化 → shell -c 递归（命中则合并子报告，跳过本段其余
    检查）→ 内联解释器与控制流标注 → 规则匹配（命中 BLOCK 提升整链
    blocked）→ 路径提取。

    Args:
        command: 待分析命令全文。
        dialect: shell 方言。
        depth: 当前递归深度（shell -c 每展开一层 +1）；达到 _MAX_DEPTH
            时只标注 nested-shell 不再展开，防止深层嵌套滥用。

    Returns:
        CommandReport；递归产生的子报告由调用方合并（blocked 与
        unparsable 取或，flagged 与 referenced_paths 追加）。
    """
    report = CommandReport()
    if depth >= _MAX_DEPTH:
        report.flagged.append(
            FlaggedCommand(
                command, "nested-shell", _FLAG, "嵌套 shell 调用层级过深，内容不再展开"
            )
        )
        return report

    for construct in _unanalyzed_constructs(command, dialect):
        report.flagged.append(
            FlaggedCommand(command, "unanalyzed-construct", _FLAG, construct)
        )

    for segment in _split_chain(command, dialect):
        try:
            tokens = _tokenize(segment, dialect)
        except ValueError:
            report.unparsable = True
            continue
        if not tokens:
            continue
        program = _normalize_program(tokens[0], dialect)

        inner = _inner_shell_invocation(program, tokens)
        if inner is not None:
            sub = _analyze(inner[1], inner[0], depth + 1)
            report.blocked = report.blocked or sub.blocked
            report.unparsable = report.unparsable or sub.unparsable
            report.flagged.extend(sub.flagged)
            report.referenced_paths.extend(sub.referenced_paths)
            continue

        if _is_inline_interpreter(program, tokens):
            report.flagged.append(
                FlaggedCommand(
                    segment,
                    "inline-interpreter",
                    _FLAG,
                    f"内联解释器执行（{program}），内容不可分析",
                )
            )
        if program in _CONTROL_FLOW_KEYWORDS[dialect]:
            report.flagged.append(
                FlaggedCommand(
                    segment,
                    "control-flow",
                    _FLAG,
                    "包含控制流结构，循环/分支体内的命令未完全分析",
                )
            )

        lowered = [token.lower() for token in tokens]
        for rule in RULES:
            if dialect not in rule.dialects:
                continue
            if _matches(rule.pattern, program, lowered):
                report.flagged.append(
                    FlaggedCommand(segment, rule.name, rule.verdict, rule.reason)
                )
                if rule.verdict is _BLOCK:
                    report.blocked = True

        report.referenced_paths.extend(_extract_paths(program, tokens))
    return report


def _split_chain(command: str, dialect: ShellDialect) -> List[str]:
    """按链式运算符把命令拆成若干子命令（引号感知）

    自写扫描器而非 shlex.punctuation_chars（Python 3.12+，项目兼容
    3.10）。逐字符扫描并维护引号状态：单双引号内的运算符、被转义的
    运算符都不算分隔符——误报的根源是不尊重引号（如 echo "a && b"），
    本函数从拆链起就避开这一类。bash 转义符为反斜杠，pwsh 为反引号
    （单引号内均无转义）。

    Args:
        command: 命令全文。
        dialect: 决定运算符集合（见 _CHAIN_OPERATORS；pwsh 的 & 是
            调用运算符而非分隔符）。

    Returns:
        子命令文本列表（已 strip，空段丢弃；保留原始引号字符——
        引号剥离是 _tokenize 的职责）。

    示例:
        "ls && echo "a;b" | wc"（bash）→ ["ls", "echo "a;b"", "wc"]
    """
    escape_char = "`" if dialect is _PWSH else "\\"
    operators = _CHAIN_OPERATORS[dialect]
    segments: List[str] = []
    current: List[str] = []
    in_single = in_double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if not in_single and ch == escape_char and i + 1 < len(command):
            current.extend((ch, command[i + 1]))
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            operator = next((op for op in operators if command.startswith(op, i)), None)
            if operator is not None:
                segments.append("".join(current))
                current = []
                i += len(operator)
                continue
        current.append(ch)
        i += 1
    segments.append("".join(current))
    return [s.strip() for s in segments if s.strip()]


def _tokenize(segment: str, dialect: ShellDialect) -> List[str]:
    """把单条子命令分成 token 列表（引号剥离，转义展开）

    bash 用 shlex（POSIX 语义成熟）；pwsh 用本文件内的自写分词器——
    shlex 会把 Windows 路径的反斜杠当转义符吃掉（C:\\temp → C:temp），
    pwsh 侧必须保留反斜杠。pwsh 分词器的简化：不处理单引号双写转义
    与 $ 展开（已知边界，见 security.md）。

    Args:
        segment: 单条子命令文本（不含链式运算符）。
        dialect: shell 方言。

    Returns:
        token 列表；空段返回空列表。

    Raises:
        ValueError: 引号不配对（调用方据此置 unparsable，fail-closed）。

    示例:
        "echo "hello world""（bash）→ ["echo", "hello world"]
        "echo x > C:\\temp\\out.txt"（pwsh）→ ["echo", "x", ">", "C:\\temp\\out.txt"]（反斜杠保留）
    """
    if dialect is _BASH:
        return shlex.split(segment, posix=True)
    words: List[str] = []
    current: List[str] = []
    in_single = in_double = False
    i = 0
    while i < len(segment):
        ch = segment[i]
        if not in_single and ch == "`" and i + 1 < len(segment):
            current.append(segment[i + 1])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch.isspace() and not in_single and not in_double:
            if current:
                words.append("".join(current))
                current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if in_single or in_double:
        raise ValueError("引号不配对")
    if current:
        words.append("".join(current))
    return words


def _normalize_program(token: str, dialect: ShellDialect) -> str:
    """把 argv[0] 规范化为可匹配的命令名

    步骤：路径分隔符统一（\\ → /）→ 去路径前缀 → 小写（Windows 命令
    大小写不敏感；bash 命令约定小写，且大小写不敏感比较对拦截更有利）
    → 去 Windows 可执行后缀（.exe / .com）→ pwsh 查别名表（借鉴
    Claude Code 的别名规范化）。

    Args:
        token: 原始 argv[0]（引号已由 _tokenize 剥离）。
        dialect: shell 方言（仅 pwsh 查别名表）。

    Returns:
        规范化后的命令名（全小写），供规则匹配与特殊程序识别使用。

    示例:
        "/usr/bin/mkfs"           → "mkfs"
        "./rm"                    → "rm"
        "C:\\Windows\\format.com" → "format"
        "Remove-Item"             → "remove-item"
        "gci"（pwsh 方言）        → "get-childitem"
    """
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith((".exe", ".com")):  # Windows 可执行后缀（如 format.com）
        name = name[:-4]
    if dialect is _PWSH:
        name = _PWSH_ALIASES.get(name, name)
    return name


def _matches(pattern: Tuple[str, ...], program: str, lowered: List[str]) -> bool:
    """判断规则的 pattern 是否命中当前子命令（token 前缀匹配）

    语义：pattern 是 token 序列的前缀——pattern[0] 与规范化后的
    argv[0]（program）比较，其余位置与原始 token 的小写形式比较
    （Windows 命令行整体大小写不敏感；bash 标志位的大小写变体如
    -rf / -Rf 也借此一并覆盖）。pattern 之后的多余 token 不影响命中。

    局限（有意为之）：不按位置重排匹配——目标在前的变形（如
    "rm / -rf"）不命中，见 security.md 的已知边界。

    Args:
        pattern: 规则的 token 前缀。
        program: _normalize_program 的输出。
        lowered: 全部 token 的小写列表。

    Returns:
        命中返回 True。

    示例:
        pattern ("git", "push", "--force") 命中 "git push --force origin main"
        pattern ("rm", "-rf", "/") 不命中 "rm -rf ./build"（"/" ≠ "./build"）
    """
    if len(lowered) < len(pattern) or pattern[0] != program:
        return False
    return all(p == t for p, t in zip(pattern[1:], lowered[1:]))


def _inner_shell_invocation(
    program: str, tokens: List[str]
) -> Tuple[ShellDialect, str] | None:
    """识别 shell 系内联执行（bash -c / pwsh -Command 等），取出内层命令

    shell 系的 -c 参数本身就是一条命令，可以且应当递归分析——这是
    bash -c "rm -rf /" 这类包裹写法无法绕过护栏的关键。内层方言由
    program 决定而非沿用外层：bash 命令串里的 pwsh -Command 会按
    PWSH 递归（见 _RECURSIVE_SHELLS）。

    Args:
        program: _normalize_program 的输出。
        tokens: 当前子命令的 token 列表。

    Returns:
        (内层方言, 内层命令串)；非 shell 内联执行、或 -c 后缺少命令串
        参数时返回 None（后者按普通命令继续走规则匹配）。

    示例:
        ["bash", "-c", "rm -rf /"]       → (BASH, "rm -rf /")
        ["pwsh", "-Command", "Get-Date"] → (PWSH, "Get-Date")
        ["bash", "--help"]               → None
    """
    spec = _RECURSIVE_SHELLS.get(program)
    if spec is None:
        return None
    inner_dialect, flags = spec
    for i, token in enumerate(tokens[1:], start=1):
        if token.lower() in flags and i + 1 < len(tokens):
            return inner_dialect, tokens[i + 1]
    return None


def _is_inline_interpreter(program: str, tokens: List[str]) -> bool:
    """判断是否是不可递归的解释器内联执行（python -c、node -e 等）

    与 shell -c 不同，这类参数是另一种语言的源码，护栏无法分析——
    命中后由调用方 FLAG「内联解释器执行，内容不可分析」，如实暴露给
    审批者（fail-visible），而不是假装安全或一禁了之（python -c 跑
    一次性脚本对编码 Agent 是常见合法操作）。

    Args:
        program: _normalize_program 的输出。
        tokens: 当前子命令的 token 列表（扫描标志位是否出现）。

    Returns:
        命中返回 True。

    示例:
        ["python", "-c", "import os"] → True
        ["node", "--eval", "1+1"]     → True
        ["python", "script.py"]       → False
    """
    flags = _INLINE_INTERPRETERS.get(program)
    return bool(flags) and any(t.lower() in flags for t in tokens[1:])


def _unanalyzed_constructs(command: str, dialect: ShellDialect) -> List[str]:
    """检测命令中护栏不展开分析的结构，返回需人工确认的描述列表

    覆盖：命令替换 $(...)（两种方言）、反引号命令替换（仅 bash——
    pwsh 的反引号是转义符）。引号感知：单引号内的文本是字面量，
    不误报；转义符后的字符跳过。

    设计立场是 fail-visible：检测到未分析结构时如实标注、把命令
    全文交给审批者判断，而不是假装分析过（fail-silent）。

    Args:
        command: 命令全文（未拆链，在原始文本上扫描）。
        dialect: 决定转义符（bash 反斜杠 / pwsh 反引号）与是否检测
            反引号替换。

    Returns:
        去重后的描述列表（每条是一句用户可读的提示文案）。

    示例:
        "echo $(whoami)"   → ["包含命令替换 $(...)，展开结果未分析，请人工确认"]
        "echo '$(whoami)'" → []（单引号内是字面量）
        "echo `date`"      → ["包含反引号命令替换，..."]（仅 bash）
    """
    found: List[str] = []
    escape_char = "`" if dialect is _PWSH else "\\"
    in_single = in_double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if not in_single and ch == escape_char:
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single:
            if command.startswith("$(", i):
                found.append("包含命令替换 $(...)，展开结果未分析，请人工确认")
            elif dialect is _BASH and ch == "`":
                found.append("包含反引号命令替换，展开结果未分析，请人工确认")
        i += 1
    return list(dict.fromkeys(found))


def _extract_paths(program: str, tokens: List[str]) -> List[str]:
    """提取子命令引用的文件路径：cd 目标与重定向目标

    针对两类围栏后门：
    - cd 目标（cd /etc && rm ... 把工作区语义挪到别处）——取 cd 系
      程序（见 _CD_PROGRAMS）的第一个非标志参数；
    - 重定向目标（echo x > /etc/important 绕过写文件工具直接落盘）——
      识别 >、>>、< 及其附着形式（2>err.txt），目标缺失时取下一 token。

    忽略无实际文件的目标：/dev/null 等设备文件、NUL、文件描述符
    形式（2>&1、<&3）。

    Args:
        program: _normalize_program 的输出（cd 识别已含 pwsh 别名
            set-location）。
        tokens: 当前子命令的 token 列表。

    Returns:
        原始路径字符串列表（保留原文；内/外分类交 path_validator——
        护栏只做提取，不做路径判定）。

    示例:
        ["cd", "/etc"]              → ["/etc"]
        ["echo", "x", ">", "o.txt"] → ["o.txt"]
        ["cmd", "2>/dev/null"]      → []（设备文件忽略）
        ["cmd", "2>&1"]             → []（文件描述符形式忽略）
    """
    paths: List[str] = []
    if program in _CD_PROGRAMS:
        for token in tokens[1:]:
            if token not in _CD_FLAGS:
                paths.append(token)
                break
    for i, token in enumerate(tokens):
        match = _REDIRECT_RE.match(token)
        if match is None:
            continue
        target = match.group(2)
        if not target and i + 1 < len(tokens):
            target = tokens[i + 1]
        if not target or target.startswith("&") or target.lower() in _REDIRECT_IGNORE:
            continue
        paths.append(target)
    return paths
