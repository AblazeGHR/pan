# 开发新的 CLI Adapter

> **适用范围**：给 Pan 接入一个新的 Agent CLI（cbc / kimi / opencode / claude / codex 之外的新 CLI）。
> **读者**：后端 / 全栈开发者。产品侧「哪些 adapter 在积极维护」见 [README](../README.md)。
>
> **重要声明：本文描述的接口以 2026-10-18 基线（main `044f022a`）的源码为准。协议随实现持续演进，
> 落地前必须重新核对 [`packages/core/adapters/base.py`](../packages/core/adapters/base.py) 与
> [`packages/core/worker.py`](../packages/core/worker.py) 的当前签名；本文给出的最小示例同样可能过期，
> 以真实源码为唯一权威。**

## 0. 先读这些（复用，不重复造轮子）

| 文档 | 内容 |
|---|---|
| [adapter-architecture.md](design/adapter-architecture.md) | 架构研究：协议分组、执行模式演进、**§8 各 adapter 模式差异表**（接新 CLI 前先对照） |
| [adapter-p1-oneshot.md](design/adapter-p1-oneshot.md) | `execution_modes` / `oneshot_args` 的设计与语义边界 |
| [cli-adapter-special-behaviors.md](references/cli-adapter-special-behaviors.md) | 各 CLI 已踩过的坑（.CMD shim、stdin EOF、事件命名、存储布局……） |
| [plugins/DEVELOPING.md](plugins/DEVELOPING.md) | **插件开发**（MCP manifest / capability 协议）。与 CLI adapter 是两回事，不要混用 |
| [TESTING.md](TESTING.md) | 测试环境与数据目录隔离要求 |

一个 CLI adapter = **一个 adapter 类**（argv 拼装 + 事件解析 + enrich）+ **一个 sessions 模块**（原生会话存储读写），
在 `packages/core/adapters/__init__.py` 各注册一行即可，不需要改 worker / server 的分发逻辑。

## 1. 目录结构与注册

```
packages/core/adapters/
├── base.py            # CliAdapter / SessionsProvider 协议（唯一定义处）
├── registry.py        # register / get_adapter / *_sessions_provider
├── __init__.py        # 内置 adapter 启动注册（新 adapter 在这里加两行）
├── resolution.py      # resolve_execution_mode(adapter, session)
├── validation.py      # Session 配置的能力校验（AdapterCapabilityError）
├── mcp.py             # MCP 描述符共享 helper（build_mcp_servers / write_mcp_json）
├── cbc/  kimi/  opencode/  claude/  codex/
│   ├── adapter.py     # adapter 类（CliAdapter 实现）
│   ├── sessions.py    # SessionsProvider 实现（模块级函数）
│   └── wrapper.py     # 仅 wrapper 型 adapter 需要（kimi/opencode/codex）
```

注册两步（`packages/core/adapters/__init__.py`）：

```python
from .example import ExampleAdapter
from .example import sessions as example_sessions

register("example", ExampleAdapter())
register_sessions_provider("example", example_sessions)
```

- `get_adapter(name)` 不存在时抛 `KeyError`；面向调用方的入口统一用
  `validation.resolve_adapter(name)`（抛 `UnknownAdapterError`，带可用列表，不会变成 HTTP 500）。
- adapter 实例应**无状态**（除配置常量与缓存），可被多个 worker 共享。

## 2. CliAdapter 协议（必实现面）

协议定义在 `base.py`（`runtime_checkable` Protocol，**不需要继承**，满足签名即可）。按职责分六组：

### 2.1 元信息（能力声明，驱动 UI 与校验）

| 成员 | 语义 |
|---|---|
| `name` | 唯一 adapter 名（Session.adapter、URL、注册表键） |
| `default_model` / `supported_models` | 模型默认值 / 可选列表；`supported_models` 为空表示不校验 |
| `effort_values` | 允许的 effort 值（空 = 不支持） |
| `permission_modes` | `[{"value": ..., "label": ...}]` |
| `default_permission_mode` | 默认权限模式 |
| `supports_resume` / `supports_fork` | resume / fork 能力（fork 也可由 sessions provider 完成，此时 adapter 侧返回 True） |
| `supported_settings` | 前端可见的设置项：`model` / `permissionMode` / `thinking` / `effort` / `modelContextWindow` / `modelAutoCompactTokenLimit`。**未声明 = 禁止写入 Session**（`validation.require_setting` 拒绝，不再伪成功） |
| `execution_modes` | `["stream"]` / `["oneshot"]` / `["stream","oneshot"]`。**这是 worker 驱动 adapter 的传输契约，不是 CLI 内部机制**：wrapper 型 adapter 内部哪怕逐条调一次性 CLI，对 worker 也只有 `stream` |

### 2.2 进程启动（argv 拼装）

| 方法 | 语义 |
|---|---|
| `resolved_cli_argv()` | 不 spawn，返回可执行组件（供 `cli_diagnostics` 预检与 `.CMD` shim 解析） |
| `base_args()` | stream 长驻基础 argv |
| `model_args(s)` / `thinking_args(s)` / `effort_args(s)` / `permission_mode_args(s)` | 各项设置 → argv 片段；不支持的项返回 `[]` |
| `resume_args(s)` | `s.cli_session_id` 存在时的续接片段 |
| `fork_args(s)` | 多数 adapter 返回 `[]`（fork 由 server branch 端点 + sessions provider 完成） |
| `build_spawn_args(s, extra_args)` | stream 模式完整 argv（worker `_spawn_process` 调用） |
| `oneshot_args(s, text)` | oneshot 模式完整 argv，**prompt 作末参**；仅 `"oneshot" in execution_modes` 时会被调用，否则返回 `[]` |

### 2.3 stdin 编码

`encode_user_message(text) -> bytes`：写入长驻进程 stdin 的一条消息（通常是一行 JSON + `\n`）。
wrapper 型 adapter 这里是 wrapper 自有协议（如 `{"text": ...}`），**必须与 wrapper.py 的读取侧一致**。

### 2.4 stdout 事件解析（通用事件模型）

`parse_event(line) -> dict | None` 解析一行 stdout；`None` = 跳过。其余成员：

| 方法 | 语义 |
|---|---|
| `event_type(event)` | 事件类型串 |
| `is_init_event(event)` / `extract_session_id(event)` / `extract_model(event)` | init 事件 → worker 首次绑定 `cli_session_id` / `model`（幂等，只在为空时写） |
| `is_assistant_event(event)` / `extract_assistant_blocks(event)` | assistant 事件 → 历史块（`{"role": "assistant/thinking/tool", "content": ...}`）。**delta 类增量事件必须让 `is_assistant_event` 返回 False**，只走实时广播、不进历史 |
| `is_result_event(event)` / `is_result_error(event)` / `extract_result_text(event)` | 终态事件。wrapper 型 CLI 没有原生 result 时，由 wrapper 合成一条（见 kimi/opencode wrapper） |

### 2.5 takeover 与 enrich

- `takeover_command(s) -> list[str]`：交互式接管命令（终端内 resume 既有会话）。**不要重注入 `--system-prompt`**（会被当成一条 user 消息重复）；cwd 由 server 终端设置。
- `enrich_after_result(s) -> list[dict] | None`：每轮结果后从 CLI 原生存储补记 token/credit。stream 与 oneshot 两条 result 路径都会调用。返回 `None` = 无数据；读取失败可抛异常，由 Session-scoped retry job 记录重试。

### 2.6 可选能力位（worker 用 `getattr` 探测，缺省安全默认）

| 能力 | 语义 |
|---|---|
| `supports_spawn_system_prompt` | stream spawn 的长驻进程原生接受 `--system-prompt <text>`。False/缺省时 worker 退化为首条消息注入——**强塞不认识的 flag 会让子进程 argparse 退出，表现为「发消息后永不回复」** |
| `supports_spawn_system_prompt_file` | 同上变体：prompt 写 session workdir 下临时文件、只传路径（适配 Windows 命令行长度限制）；adapter 负责读取并删除 |
| `supports_native_interrupt` | 支持 out-of-band 中断（不杀整个进程） |
| `supports_native_steer` / `supports_steer_receipt` / `steer_requires_run_id` / `normalize_steer_receipt` / `encode_control_message` | native Steer，见 §6（高风险区） |
| `model_efforts` | 按 model 收窄的 effort 值域（codex 用） |

## 3. 执行模式与生命周期

### 3.1 模式解析

`resolve_execution_mode(adapter, s)`（`resolution.py`）合并 `adapter.execution_modes` 与
`s.adapter_config["output_mode"]`：

- 显式且合法 → 直接用；未设置/非法 → 多模式取 `"stream"`，单模式取其唯一项；
- **`system_prompt` 超过 `SYSTEM_PROMPT_ARG_MAX_CHARS`（4096）且 adapter 同时有 stream 时，强制回落 stream**——超长 prompt 不能进 Windows CreateProcess 命令行。

server 侧对称约束：`outputMode` 不在 `execution_modes` 内 → 400（`validation.validate_output_mode`）。

### 3.2 stream 生命周期（`worker.py`）

```
_create_worker → _spawn_process(cwd=s.workdir, stdin/stdout=PIPE, stderr=STDOUT)
  → _read_stdout 循环：
      parse_event 逐行解析
        ├─ init 事件      → 绑定 cli_session_id / model（幂等）
        ├─ assistant 事件 → extract_assistant_blocks 追加 history（防抖落盘）+ worker.stream 广播
        ├─ result 事件    → 终态：done / error（is_result_error）/ cancelled；
        │                    _persist_terminal_state → worker.result 广播 → 报告入队 → enrich_after_result
        └─ 其它           → worker.stream 实时广播（含 delta）
      stdout EOF → 进程退出：
        · 已有正常 result（last_result 非 error）→ 忽略非零退出码
        · 否则 → worker.crashed 广播 + worker.zombie + 异常退出报告（running/queued 状态下）
        · flush 防抖历史 → 从 workers 表移除 → 安排 session 恢复
```

关键不变量（改动 worker 前必读）：

- **`_terminal_handled` 闩锁**：result 之后到达的迟滞帧不得追加进历史或挂到下一个队列项的 taskSeq；
- **重复 result 事件被忽略**（防双跑）；
- **任务完成用独立 Event（`_task_done`）而非轮询 status**——否则旧任务的 idle 会覆盖新任务的 running，
  多条消息同时在管道里飞行，result 与 taskSeq/历史错位；
- **enrich 在 stream / oneshot 两条 result 路径都会调用**，新 adapter 不需要自己找调用点。

### 3.3 oneshot 生命周期（`_consumer_oneshot`）

```
adapter.oneshot_args(s, text) → spawn（cwd=s.workdir，无 stdin）
  → 读 stdout（超时 kill、16MB 上限）→ 同 §2.4 事件模型逐行解析
  → result / init(session_id) / assistant 收集 → cli_session_id 幂等绑定
  → 失败面：超时 / 非零退出 / 零输出 → adapter.extract_oneshot_error(output)（可选）或通用启发式
  → worker.stream 实时广播 + worker.result + 报告入队 + enrich（与 stream 一致）
```

### 3.4 队列语义（adapter 不直接感知，但影响验收）

- 队列真源是 Session 的持久化 `queue_pending`；`pending_signal` 只是唤醒通道；
- **队列项保持原 ID**：`queueItemId`/`id` 在重投、恢复、steer 全过程不变（`worker.py: _queue_item_id`），
  steer / 幂等 / 去重都靠它；不要在任何路径上新造 ID；
- `taskId` 幂等：同 taskId 重发不重复入队（pending → 等待；终态 → 回放缓存结果）。

## 4. 事件解析要点

- CLI 没有结构化 stdout 时（纯文本），在 adapter 内把整行包成一条 assistant/result 事件，**不要改 worker**；
- 事件里携带 session id 的（如 opencode 每事件带 `sessionID`）：`is_init_event` 认它，worker 只首次写入；
- 大行 / 分块读取由 worker 的 `_iter_stdout_lines` 处理，adapter 无需关心；
- stdout 无 model 字段的 CLI：`extract_model` 返回 `None`，model 由「用户显式指定 + enrich 回填」两处兜底。

## 5. MCP 与安全边界

- **共享 helper**：`adapters/mcp.py` 的 `build_mcp_servers(s)` / `write_mcp_json(path, s)` 构造
  `{"mcpServers": ...}` 描述符（含 pan/pan-qq 身份注入 `PAN_AGENT_SESSION_ID/TITLE`），优先复用；
- 四种已验证落点（按 CLI 能力选一，详见 cli-adapter-special-behaviors.md）：
  1. `--mcp-config <path>` 文件（cbc / claude）：写到 Pan 自有 `data/mcp-configs/<sid>.mcp.json`，**绝不写 workdir**；
  2. 隔离 HOME 环境变量（kimi：`KIMI_CODE_HOME`）绕过 folder-trust 门禁；
  3. 项目级配置文件合并写（opencode：`opencode.json(c)` 的 `mcp` 段，定位 git 根、合并而非覆盖）；
  4. `-c key=value` 内联覆盖（codex：`mcp_servers.<name>...`，零文件污染、不触碰 auth.json）。
- **信任边界**：项目级配置需要 workdir 被信任；非交互进程无法应答信任提示的 CLI 不要走项目级；
- **日志红线**：`_log_mcp_launch` 只记 server 名 / 可执行路径 / env **键**，不记 argv 值——
  prompt 与 system prompt 可能含用户数据，新 adapter 的日志同样遵守；
- **Windows `.CMD` shim**：`shutil.which()` 返回的 npm shim 经 `cmd.exe /c` 启动会把中文参数按 ANSI
  代码页重切分（乱码 / 卡 running）。解析到真实入口（`node <entry.js>` 或原生 exe），
  支持 `PAN_<NAME>_PATH` 环境变量覆盖（见各 adapter `_resolve_*_argv`）。

## 6. native Steer（高风险区，先读语义红线再实现）

Steer = 任务运行中把补充指令送进当前回合（区别于排队）。worker 通过 adapter 的三个能力位分流：

| 能力位 | 含义 |
|---|---|
| `supports_native_steer` | 有 out-of-band steer 通道 |
| `supports_steer_receipt` | provider 会对 steer 写入回执（`pan.steer_receipt`） |
| `steer_requires_run_id` | steer 必须绑定当前活跃 run（cbc：`control_request.expected_request_id`） |

**语义红线（worker.py `_steer_receipt_result` + 各 adapter `normalize_steer_receipt` 强制）：**

1. **只有 `ok=True` 且 `outcome="accepted"` 的显式回执才算成功**；
2. provider 显式结构化拒绝（`ok=False, outcome="rejected"`）→ `rejected`；
3. **其余一切——回执缺失、响应无法识别、类型不符、legacy 无类型失败——一律 `unknown`，
   绝不允许从散文 / 退出码 / 静默中「推断」成功，也不允许把 unknown 当 accepted 落账**；
   调用方对 unknown 的处理是「保留原请求、不自动重发」；
4. `steer_requires_run_id` 的 adapter：run 变更 / turn 结束 / session 不匹配 → 直接拒绝握手
   （`_prepare_steer_control` 抛错，而不是发出一个必然失效的控制帧）。

参考实现：cbc（`cbc/adapter.py: encode_control_message` / `normalize_steer_receipt`）——
stdin 写 `{"type":"control_request","request_id":...,"request":{"subtype":"steer",...}}`，
把 provider 的 `control_response` 归一化为 `pan.steer_receipt`；未识别响应的 outcome 固定
`"unknown"` 且 error 明示需要的最低 CLI 版本。claude 的 steer 是「文档化的 stdin 边界」：
再写一条 user message（无回执位）；codex 走 app-server `turn/steer`（有回执）。
cbc 的 native TUI 变体（`cbc/native_adapter.py`）显式关闭 steer 能力位——**没有回执就不要声明
`supports_steer_receipt`**。

## 7. SessionsProvider（导入 / branch / rename）

`sessions.py` 是实现 **模块级函数**（不是类），协议见 `base.py: SessionsProvider`：

| 函数 | 语义 |
|---|---|
| `list_sessions(cwd=None)` | 列原生会话（供导入浏览） |
| `parse_history(session_id, cwd=None)` | 原生 transcript → Pan history 块 |
| `get_raw_usage(session_id, cwd=None)` | 原生用量（enrich 数据源） |
| `get_session_title(...)` / `write_custom_title(...)` | 标题读写 |
| `fork_session(parent_id, name, cwd=None)` | fork：无原生 flag 时用存储级复制（JSONL 复制 / DB 行复制，参照 claude/opencode/codex） |
| `session_exists(session_id, cwd)`（可选） | import 存在性防御；不提供则跳过 guard |

注册后，通用端点 `GET/POST /api/adapters/{adapter}/sessions[/import]` 自动生效，
**不需要**在 server 写 import/branch/rename 分派（旧 `/api/cbc/*` 等仅为薄包装）。

## 8. 配置 API 与前端必要集成

- `GET /api/adapter/config?adapter=<name>`（server.py）返回
  `models / defaultModel / effortValues / modelEfforts / permissionModes / defaultPermissionMode /
  supportedSettings / executionModes` —— **adapter 能力声明的唯一出口**；
- `GET /api/adapters` 返回全部已注册 adapter（name / defaultModel / supportsResume / supportsFork / supportsSteer）；
- 前端按 `supportedSettings.includes(name)` 显隐设置控件（`InputRow.tsx: supportsSetting`、
  `SettingsPopover.tsx`、`NewSessionModal.tsx`、`utils/quickNewSession.ts`）；output mode 选择器按
  `executionModes.length > 1` 渲染。**纯后端 adapter 无需改前端即可工作**（设置项不出现而已）；
  只有要暴露新设置键时才动前端：`packages/web/src/types/index.ts`（`AdapterConfig`）、
  `services/api.ts` 解析，改完在 `packages/web/` 执行 `pnpm build`（CODEBUDDY.md 要求）；
- Session 创建 / 更新的能力校验统一走 `validation.validate_session_settings` /
  `sanitize_adapter_config`（handoff 跨 adapter 复制时清洗不兼容设置）。

## 9. 最小实现骨架（签名以 base.py 为准，落地前重新核对）

下面是一个「单模式 stream + 简单 JSONL stdout」CLI 的最小形态，省略了错误处理与缓存，
只展示**协议面的最小集合与真实签名**。它不是可注册的产品代码，仅作起步参照：

```python
"""examplecli adapter 最小骨架（示意，非产品代码）。"""

from __future__ import annotations

from ...session import Session
from ..mcp import write_mcp_json


class ExampleAdapter:
    # ── 元信息 ──
    name = "example"
    execution_modes = ["stream"]          # worker 只起长驻进程
    supports_resume = False
    supports_fork = False
    effort_values = []                    # 空 = 不支持 effort
    permission_modes = []                 # 空 = 不支持权限模式
    default_permission_mode = ""
    supported_settings = ["model"]        # 前端只出现 model

    _DEFAULT_MODEL = "example-model-1"

    @property
    def default_model(self) -> str:
        return self._DEFAULT_MODEL

    @property
    def supported_models(self) -> list[str]:
        return [self._DEFAULT_MODEL]

    # ── 进程启动 ──
    def resolved_cli_argv(self) -> list[str]:
        return ["examplecli"]             # 真实实现解析 .CMD shim / 环境变量覆盖

    def base_args(self) -> list[str]:
        return ["examplecli", "serve", "--jsonl"]

    def model_args(self, s: Session) -> list[str]:
        m = s.model or self.default_model
        return ["--model", m] if m else []

    def thinking_args(self, s: Session) -> list[str]:
        return []

    def effort_args(self, s: Session) -> list[str]:
        return []

    def permission_mode_args(self, s: Session) -> list[str]:
        return []

    def resume_args(self, s: Session) -> list[str]:
        return []

    def fork_args(self, s: Session | None = None) -> list[str]:
        return []

    def mcp_args(self, s: Session) -> list[str]:
        # adapter 私有 helper（非协议成员）：CLI 不支持 MCP flag 时保持返回 []
        servers = s.adapter_config.get("mcp_servers")
        if not servers:
            return []
        from ...session import SESSION_DIR
        path = SESSION_DIR.parent / "mcp-configs" / f"{s.id}.mcp.json"
        if write_mcp_json(path, s) is None:
            return []
        return ["--mcp-config", str(path)]

    def build_spawn_args(self, s: Session,
                         extra_args: list[str] | None = None) -> list[str]:
        args = self.base_args()
        args.extend(self.model_args(s))
        args.extend(self.mcp_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    def oneshot_args(self, s: Session, text: str) -> list[str]:
        return []                        # execution_modes 无 oneshot → 不会被调用

    # ── stdin 编码（与 CLI 的输入协议一致） ──
    def encode_user_message(self, text: str) -> bytes:
        import json
        return (json.dumps({"text": text}, ensure_ascii=False) + "\n").encode("utf-8")

    # ── stdout 事件解析 ──
    def parse_event(self, line: str) -> dict | None:
        import json
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        return event if isinstance(event, dict) else None

    def event_type(self, event: dict) -> str:
        return event.get("type", "")

    def is_init_event(self, event: dict) -> bool:
        return event.get("type") == "init"

    def extract_session_id(self, event: dict) -> str | None:
        return event.get("session_id")

    def extract_model(self, event: dict) -> str | None:
        return event.get("model")

    def is_assistant_event(self, event: dict) -> bool:
        return event.get("type") == "assistant"

    def extract_assistant_blocks(self, event: dict) -> list[dict]:
        text = event.get("text") or ""
        return [{"role": "assistant", "content": text}] if text else []

    def is_result_event(self, event: dict) -> bool:
        return event.get("type") == "result"

    def is_result_error(self, event: dict) -> bool:
        return bool(event.get("is_error", False))

    def extract_result_text(self, event: dict) -> str | None:
        return event.get("result")

    # ── takeover / enrich ──
    def takeover_command(self, s: Session) -> list[str]:
        return []                        # 无 cli_session_id 时返回 []

    def enrich_after_result(self, s: Session) -> list[dict] | None:
        return None                      # 无原生用量存储
```

CLI 若没有 stdin 长驻协议（如 `-p` 一次性），两条路：写 `wrapper.py` 把「长驻 + 内部逐条一次性」
包成 stream 形态（kimi/opencode/codex 模式），或实现 `oneshot_args` 走 worker 通用 oneshot consumer
（cbc/claude 在 `execution_modes` 里追加 `"oneshot"`；只支持一次性进程的 CLI 可声明
`execution_modes = ["oneshot"]`——机制已支持，但当前内置 adapter 均至少含 stream）。
wrapper 的坑（子进程 `stdin=DEVNULL`、stdin 二进制读 + UTF-8 显式解码、stdout UTF-8 写出）
见 cli-adapter-special-behaviors.md §1。

## 10. 已注册 adapter 一览（2026-10 基线，以 `__init__.py` 为准）

| adapter | execution_modes | supported_settings | native steer | 传输形态 |
|---|---|---|---|---|
| cbc | stream + oneshot | model / permissionMode / effort / thinking | 有（回执 + run id 绑定） | 原生长驻；oneshot 逐任务 spawn |
| claude | stream + oneshot | model / permissionMode / effort | 有（stdin 边界，无回执位） | 原生长驻；oneshot 逐任务 spawn |
| codex | stream | model / permissionMode / effort / modelContextWindow / modelAutoCompactTokenLimit | 有（app-server turn/steer 回执） | wrapper → `codex app-server --stdio` |
| kimi | stream | model | 无 | wrapper 长驻（内部 `kimi -p`） |
| opencode | stream | model / permissionMode / effort / thinking | 无 | wrapper 长驻（内部 `opencode run`） |

维护状态（哪些在积极维护）以 README 为准，本表只描述技术形态。

## 11. 测试与 PR 核对

**测试**（环境与数据隔离要求见 [TESTING.md](TESTING.md)；从 Agent / 后台 Job 运行时用
`python scripts/run_tests_isolated.py -- <命令>`）：

- 参照 `tests/test_claude_adapter.py`：纯单测（argv 拼装 / shim 解析 / 事件解析 / takeover /
  enrich 缓存桥接）+ `skipif` 的真实探针数据用例，CI 缺数据自动跳过；
- argv 等价性用逐元素比对钉死（参照 `tests/test_cbc_oneshot_args.py`）；
- 涉及 worker 分派 / steer 语义的，参照 `tests/test_worker_output_mode.py`、
  `tests/test_cbc_steer.py`、`tests/test_worker_cli_session_binding.py`；
- 文档类改动不要求跑全量测试；改了 `packages/web/src/` 才需要 `pnpm build`。

**PR 核对清单**：

- [ ] 协议成员签名与当前 `base.py` 逐一核对（协议会演进，不要照抄旧文档）；
- [ ] `execution_modes` 语义正确（wrapper 内部一次性不暴露 oneshot）；
- [ ] result / error / EOF 三态在真实 CLI 上验证过（含非零退出码但有完整回复的场景）；
- [ ] steer 若声明回执能力，unknown 不当成功（§6 红线）；没有回执就不声明 `supports_steer_receipt`；
- [ ] MCP 落点不写 workdir（除非 CLI 只支持项目级且已评估信任边界）；日志不出现 prompt 原文；
- [ ] `.CMD` shim / 中文参数在 Windows 实测；`PAN_<NAME>_PATH` 覆盖可用；
- [ ] `sessions.py` 六函数 + 注册两行完成，通用导入端点冒烟通过；
- [ ] 新踩到的坑回填 [cli-adapter-special-behaviors.md](references/cli-adapter-special-behaviors.md)；
- [ ] 规范文档术语合规（指代 TA/Worker 不用旧称呼，禁用词清单见 `tests/test_docs_terminology.py`）。
