# dsh (DeepSeek Harness 44.7.0) adapter

> 状态：**已实现**（2026-10-24，baseline `be46c144`）。
> 范围：`packages/core/adapters/dsh/`、注册（`adapters/__init__.py`、`cli_diagnostics.py`）、
> React ImportModal 的 dsh 标签页、`tests/test_dsh_adapter.py` / `tests/test_dsh_sessions.py`。
> 环境限制：本机 DSH **缺少凭据**（`llm-deepseek` 无 `DEEPSEEK_API_KEY`），因此
> 「模型真的回答问题」这一层未验证；协议、argv、事件、patch、MCP、resume、fork、
> 会话存储读取均有真实 CLI / 真实存储证据（见文末「证据」）。

## 1. 为什么是 wrapper 长驻

DSH headless 是「一个任务一次进程」：

```
dsh --profile headless [--json] [--session-id <id>] [task|-]
```

- 没有 stdin 流式协议；
- 没有 model / permission / system-prompt / MCP 的 CLI flag；
- 跑完一个任务即退出，`--json` 输出 NDJSON 运行事件。

Pan worker 的契约是「一个常驻进程 + stdin 逐条消息 + stdout 逐行事件」。
本 adapter 与 kimi/codex 采取同一策略：`execution_modes = ["stream"]`，由
`wrapper.py` 冒充常驻进程，**内部逐条 spawn** `dsh headless --json`，把 NDJSON
翻成 Pan 事件并合成 `result`。DSH 侧的一次性语义对 worker 透明。

## 2. 启动入口解析（避开 cmd.exe）

真实启动链（本机实测）：

```
D:/node_npm/node_global/dsh.cmd            # 转发 shim：call "D:\project\DSH\resources\runtime\cli\bin\dsh.cmd" %*
  -> D:/project/DSH/resources/runtime/cli/bin/dsh.cmd
       set ELECTRON_RUN_AS_NODE=1
       "%~dp0..\..\..\..\DeepSeek Harness.exe" --expose-internals "%~dp0..\..\..\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js" %*
```

`adapter._resolve_entry()` 读取 shim 文本（跟随 `call` 递归，最多 4 层，展开
`%~dp0`），得到 `(DeepSeek Harness.exe, app.asar/.../cli.js)`，交给 CreateProcess
直接执行，不经 cmd.exe 二次切分（中文/长参数安全，与 cbc/codex/opencode 结论一致）。

- `cli.js` 在 `app.asar` 归档内：OS 层 `is_file()` 为 False，因此判存在改为
  「asar 归档文件存在」，`resolved_cli_argv()` 只报 exe（`/api/cli/status` 不误判）。
- 覆盖：`PAN_DSH_PATH`（exe 或 shim）、`PAN_DSH_CLI_JS`（cli.js）、`config.json`
  的 `dsh.path` / `dsh.profile` / `dsh.provider` / `dsh.model` / `dsh.models` /
  `dsh.permission_mode`。
- Escaped: 解析失败时退回 shim 本身（`cmd.exe` 处理），此时不再传 `--expose-internals`。

## 3. 事件协议

`--json` 词表（源码：`@deepseek-ai/dsh-headless/lib/json-stream-*.js`）：

| DSH 事件 | 含义 | 归一为 Pan 事件 |
|---|---|---|
| `{"type":"session","sessionId","cwd"}` | 流首行，本次 run 的会话身份 | `system/init`（worker 写 `cli_session_id` / 标记 ready） |
| `status/ turn_start / step_start / step_end` | 步骤边界；`step_end` 可能带 `usage` | 原样 `status`（前端忽略，仅诊断） |
| `status/ turn_end` | 本轮结论；`reason.kind` ∈ completed / blocked / aborted / error / max-tokens | `status`（带 reason） |
| `text` | **step 提交后**的助手正文（不是 token 增量） | `assistant`（cbc 形状 `message.content[].type=text`） |
| `thinking` | 提交后的推理正文 | `assistant`（`content[].type=thinking`） |
| `tool_call` | `callId/tool/input` | `assistant`（`content[].type=tool_use`） |
| `tool_result` | `callId/status/result` | `tool_result`（不写历史，避免与 call 重复） |
| `final` | 正常收尾的最终答案（不截断） | 不直接转发；wrapper 用它合成 `result` |
| `error` | runner 级失败，**没有** final | 不直接转发（`type:"error"` 会被 worker 当瞬态错误）；合成失败 `result` |

成败判定完全按 DSH 官方规则（`headless` 文档 "Exit mapping"）：

1. runner 级 `error` 事件 → 失败（`error_source=dsh_error_event`）；
2. `turn_end.reason.kind != completed` → 失败（`dsh_turn_<kind>`，error 时带
   `error.code`，例如 `MISSING_CREDENTIAL`）；
3. 无 `turn_end` 且退出码非 0 → 失败（附 stderr 尾部，`dsh_stderr`）；
4. 其余成功：正文取 `final.text`，回退最后一条 `text`。

`mapped result` 还带 `usage`（step_end 累计）便于诊断；Session 账本走
`enrich_after_result`（见 §6）。

## 4. 会话、resume、fork

**存储布局**（源码：`dsh-session-persistence-jsonl` + `dsh-home-paths`，本机实测）：

```
$DSH_HOME/sessions/<projectKey(cwd)>/<sessionId>/session.v4.jsonl.zstd
$DSH_HOME/storages/session_projcache/sessions/<sessionId>.json
```

- `session.v4.jsonl.zstd` 是**多帧 zstd**：第一帧恰好是一行 header
  （`{"type":"session",...,"id":...}`），之后每个事件一帧（追加写）。DSH 自己
  校验「第一帧只有一行 header」，所以改写时必须逐帧重建。
- `projectKey` 算法（已逐字符移植到 `sessions.project_key`，并用三个真实目录
  验证）：分隔符 `/ \\ :` 的连续 run 折叠成 `-`；`[A-Za-z0-9._-]` 之外（含 `~`）
  编码为 `~XXXX`；包成 `--…--`、去前导 `-`、截断 251；空 cwd → `_no-cwd`。
  例：`D:\` → `--D---`；`D:\tmp\dsh probe.x_y` → `--D-tmp-dsh~20probe…`（实测
  `--D-tmp-dsh~0020probe.x_y--`）。
- `$DSH_HOME` 默认 `~/.dsh`，环境变量可覆盖（实测生效）。

**resume**：`--session-id <id>`。实测：未知 id → `{"type":"error","message":"session … does not exist"}`；
已知 id → 同一 session 从 `turn: N+1` 继续。adapter 的 `resume_args()` 返回
`["--session-id", s.cli_session_id]`；wrapper 进程内也会记住首条消息捕获的 id，
后续消息自动续接。

**fork**：`sessions.fork_session(parent, name, cwd)` 复制 `<parent>/` 到新
`session-<uuid>/`，改写 header 的 `id`（并写 `forkedFrom`），按「一行一帧」重新
压缩，并追加一条 `session/title`（user 来源，标题 = 分支名，与 rename 同一机制）；
projcache 按新 id 复制。实测：复制后的会话可被 `--session-id` 采用，且
`turn` 号继续递增 → 历史被完整读取。`supports_fork = True`。

**标题**：`get_session_title` = 日志里**最后一条** `session/title` > 投影缓存 >
首条真实用户消息（截断 80 字节）。取最后一条与 DSH 自己的 `foldSessionTitle`
一致（它也是 `findLast`），因此自动标题在前、用户重命名在后时能正确读到后者。

`write_custom_title` 走 **DSH 官方 rename 的同一机制**：向
`session.v4.jsonl.zstd` 追加一条

```json
{"type":"session/title","seq":N+1,"time":<epoch ms>,
 "data":{"title":"…","messageSeqs":[],"source":{"kind":"user"}}}
```

（`source.kind = "user"` 会把标题 **pin 住**：DSH 的自动标题生成让位，后续用户
消息也不再重新生成。）实测（2026-10-24，DSH 44.7.0，本机真实 CLI）：

1. 追加后 `dsh headless --session-id` 正常 resume（turn:2），stderr 无 corrupt；
2. 该次运行后 DSH 自己的投影缓存
   `storages/session_projcache/sessions/<sid>.json` 的 `title` **变成我们写的值**
   ——说明 DSH 接受这条事件并据此重投影，而不是覆盖它；
3. 日志里仍然只有两条 title 事件（原始 auto + 我们的 user），DSH 没有追加新标题；
4. 历史/usage 读取不受影响。

仍保持 best-effort：会话不存在、标题归一化后为空、zstd 不可用、写入失败时静默
返回（Pan 侧重命名已生效）。标题按 DSH 的 `maxTitleBytes`（80）归一化：剥掉
OSC/CSI/ESC、C0/C1 控制符与方向性不可见字符，空白折叠成一行。并发窗口：会话
日志是单写者追加文件，`_append_events` 只做「原文件大小未变」的廉价检查再追加
一帧（不做跨进程锁，DSH 本身也不加锁），因此应在会话空闲时调用。

**历史**：只取*提交*的 `assistant/message`（被 retry 丢弃的 `assistant/attempt`
不写入历史，与 DSH 自己的 `--json` 投影一致）；`user/message` 中
`source.kind != "user"` 的（runtime-context / skill reminder）跳过；
`tool/result` 不重复计入（调用以 assistant 消息里的 `tool-call` 块呈现）。

**usage**：`assistant/message.data.usage` 求和（权威，进程退出前落盘），回退
投影缓存 `tokenUsage.totals`。字段映射（DSH → Pan 账本）：
`inputTokens - cacheRead - cacheWrite → prompt_tokens`、`outputTokens → completion_tokens`、
`cacheRead/WriteTokens → cache_read/write_tokens`、`reasoningTokens → reasoning_tokens`、
`totalTokens → total_tokens`。全 0 时返回 `[]`（不污染账本）。

**zstd 依赖**：读取需要 `zstandard` 或 Python ≥ 3.14 的 `compression.zstd`；
`minimal-requirements.txt` 已声明 `zstandard>=0.25; python_version < "3.14"`
（3.14+ 走标准库）。两者都缺时 sessions provider 返回空并记一次 warning，列表里
也不再出现"读不到日志"的空壳行；**实时对话不受影响**（adapter 只依赖 CLI 的
NDJSON）。

## 5. 注入面：model / effort / permission / system prompt / MCP

DSH 把这些都放在 profile 配置里，没有 CLI flag。本 adapter 用 **`--patch` 叠加层**
（DSH launcher 原生支持，`--dump-config` 可验证），patch 文件写在 Pan 自己的数据
目录：`data/dsh-configs/<session-id>.patch.yml`（每次 spawn 重写，不改 DSH 安装
目录、不改用户 profile）。

```yaml
- id: agent-default-model
  config: { provider: deepseek-official, model: deepseek-v4-pro, reasoningEffort: low }
- insert:
    - id: pan-mcp-pan
      name: '@deepseek-ai/dsh-mcp-client'
      config: { serverName: pan, transport: stdio, command: …, args: [...], env: {...}, cwd: … }
```

- **model / effort**：DSH 的 patch 语义是「config 整体替换、不深合并」，所以
  `agent-default-model` 必须写全 provider/model（可选 reasoningEffort）。这三个
  字段就是该行的全部字段，替换安全。`provider` 默认 `deepseek-official`，可用
  `config.json` 的 `dsh.provider` 覆盖。
- **MCP**：headless bundle **不含** MCP client（`--dump-config` 只有
  `mcp-resources`），必须用 `- insert:` 在根列表追加 `@deepseek-ai/dsh-mcp-client`
  条目。`stdio` → `transport: stdio` + command/args/env/cwd；`http`/`sse` →
  `transport: streamable-http` + url/headers（DSH 只有 stdio 与 streamable-http
  两种传输，旧 SSE 未实现）。`serverName` 受 `[A-Za-z0-9_-]{1,32}` 约束，超范围
  字符会被清洗；重名由共享 helper 的 dict 语义去重（后写覆盖）。pan/pan-qq 的
  `PAN_AGENT_SESSION_ID` / `PAN_AGENT_SESSION_TITLE` / `PYTHONPATH` / `PAN_API_URL`
  由 `adapters/mcp.py` 统一注入。
- **permission mode**：没有 flag，走环境变量 `DSH_PERMISSION_MODE`
  （`sandbox-policy.mode` 与 `approval.policy` 都读它）：

  | Pan 值 | sandbox | approval | 说明 |
  |---|---|---|---|
  | `""`（default） | profile 默认（workspace-write） | ask | 需要审批的操作在 headless 下 **fail closed** |
  | `read-only` | read-only | ask | 只读，越界即失败 |
  | `workspace-write` | workdir | ask | 沙箱内可写，越界审批失败 |
  | `danger-full-access` | 无沙箱 | never | 无人值守唯一「全部可跑」的模式（默认值，与 cbc bypassPermissions / codex bypass 对齐） |

  headless profile 不 mount 任何 approval answerer（`dsh-user-approval` 文档：
  "No built-in answerer — headless or incompletely composed deployments resolve
  unavailable and fail closed"），所以 `ask` 的实际效果是拒绝。
- **system prompt**：headless 无 flag，adapter 声明
  `supports_spawn_system_prompt = False`，由 worker 走既有的「首条消息注入」路径
  （只在尚无 `cli_session_id` 时注入一次；DSH 会把该消息持久化进 session 历史，
  续接天然延续）。与 cbc 的 `--system-prompt` 差异：Pan 的系统提示词在 DSH 里是
  一条**可见的 user 消息**，而不是独立的 system 层。
- **takeover**：headless 是「跑完即退」，不能就地接管。返回交互式 profile 的续接
  命令（`--profile tui|desktop --resume <id>`），**仅当** `$DSH_HOME/profiles/<p>`
  已存在（实测未创建时 `dsh --profile tui` 报 `profile "tui" does not exist`）；
  否则返回 `[]`，上层给出「不支持 takeover」。

  **必须带 `ELECTRON_RUN_AS_NODE=1`**：DSH CLI 是
  `DeepSeek Harness.exe --expose-internals cli.js`，由 Electron 二进制以 Node 模式
  执行；缺这个变量时窗口会启动**桌面 GUI**而不是 CLI 会话。Pan 的
  `_open_terminal` 只开 PowerShell 跑命令串、不注入环境，所以：

  - 解析到 `.cmd` shim 时直接返回 shim（安装 shim 自己 `set
    "ELECTRON_RUN_AS_NODE=1"`）；
  - 只解析到 exe 时，在 `data/dsh-configs/takeover-launcher.cmd` 生成 Pan 自有
    launcher（`set "ELECTRON_RUN_AS_NODE=1"` + `"<exe>" --expose-internals
    "<cli.js>" %*`），返回 `[launcher, --profile <p>, --resume <id>]`。

  实测（本机）：launcher `-V` 输出 `0.2.1-rc.3` 并退出，未知 profile 返回
  `profile "…" does not exist` + 退出码 1 → 确实是 CLI 而非 GUI。profile 不存在时
  仍返回 `[]`；交互式 profile 本身未实机跑过（本机没有 tui/desktop profile）。

## 6. 凭据（本机阻塞项，不是 mock）

headless profile 显式禁用了托管凭据库（`credentials` / `authorization` /
`deepseek-account` 三行 `disabled: true`），因此 GUI 里保存的 key **不会被 headless
使用**。凭据解析顺序（`dsh-headless` 文档）：

1. 启动环境变量 `DEEPSEEK_API_KEY`
2. `<invocation cwd>/.env`
3. `$DSH_HOME/.env`

本机三项都没有，实测失败：

```
{"type":"status","phase":"turn_end","turn":1,
 "reason":{"kind":"error","error":{"code":"MISSING_CREDENTIAL",
 "message":"llm-deepseek: no API key for provider route \"deepseek-official\"; …"}}}
dsh: MISSING_CREDENTIAL: llm-deepseek: no API key …
```

adapter 不伪造凭据：失败会原样透出 `code` 与可执行提示（含 "export
DEEPSEEK_API_KEY"），`error_source=dsh_turn_error`。要恢复 GUI 保存的登录，用户需要在
`$DSH_HOME/profiles/headless/cordis.patch.yml` 里把上述三行改回 `disabled: false`
（DSH 官方文档给出的做法）——Pan 不代改用户 profile。

## 7. 与其他 adapter 的差异（约定层）

| 维度 | dsh |
|---|---|
| 执行模式 | `["stream"]`（wrapper 长驻，内部逐条 `dsh headless --json`） |
| 事件协议 | wrapper 把 DSH NDJSON 归一为 cbc 形状（`system/init` + `assistant` + 合成 `result`） |
| session 存储 | `$DSH_HOME/sessions/<projectKey>/<session-id>/session.v4.jsonl.zstd`（多帧 zstd）+ projcache JSON |
| resume / fork | `--session-id <id>` / 目录复制 + header 改写 + 逐帧重写（+ 追加分支名标题帧） |
| 原生标题 | 追加 `session/title`（user 来源，pin 住自动标题）；读取取日志最后一条 |
| MCP 落点 | `data/dsh-configs/<sid>.patch.yml` + `--patch`（根 insert `dsh-mcp-client`） |
| enrich 数据源 | 会话日志 `assistant/message.data.usage` 聚合（回退 projcache） |
| 凭据 | 环境变量 / `.env`（headless 忽略 GUI 凭据库） |
| takeover | `tui`/`desktop` profile 存在时：shim 或 Pan 自有 launcher（`ELECTRON_RUN_AS_NODE=1`） |
| 不可用能力 | 无 `--system-prompt`（首条消息注入）、无 approval 交互、无 SSE MCP |
| zstd 依赖 | `minimal-requirements.txt`: `zstandard>=0.25; python_version < "3.14"` |

## 8. 证据（2026-10-24，本机真实 CLI）

1. `dsh --profile headless --json "PROBE"` 的真实 NDJSON（stdout 原文）：

```
{"type":"session","sessionId":"session-6a613538-b18e-4980-b977-60000b6c5be8","cwd":"D:\\tmp\\dshprobe"}
{"type":"status","phase":"turn_start","turn":1}
{"type":"status","phase":"step_start","turn":1,"step":1}
{"type":"status","phase":"step_end","turn":1,"step":1}
{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"error","error":{"message":"llm-deepseek: no API key for provider route \"deepseek-official\"; …","code":"MISSING_CREDENTIAL"}}}
{"type":"final","text":""}
```

2. wrapper 端到端（真实 CLI，经 `wrapper.py`）：输出 `system/init` →
   `status×4` → `result(is_error=true, error_source=dsh_turn_error,
   cli_session_id=session-…)`。
3. resume：`--session-id <已知 id>` → `turn:2`；`--session-id <未知 id>` →
   `{"type":"error","message":"session … does not exist; omit --session-id …"}`。
4. fork：复制+改写后的会话被 `--session-id` 采用，`turn:3` 继续。
5. model/effort patch：会话日志 `request/header` 显示
   `{"provider":"deepseek-official","model":"deepseek-v4-pro","reasoningEffort":"low"}`。
6. MCP 注入：DSH 真实拉起 `python -m packages.mcp.server`，子进程 stderr 出现
   `Processing request of type ListToolsRequest`（握手成功）。
7. 会话读取：`/api/adapters/dsh/sessions` 同源的 provider 函数在真实
   `~/.dsh/sessions` 上列出 7 条会话（标题/cwd/消息数正确）。
8. 原生标题写入（真实 CLI）：`write_custom_title` 追加 `session/title` 后，
   `get_session_title` 立即返回新值；`dsh --session-id` resume 无 corrupt；
   DSH 重投影的 projcache `title` 变为新值；日志仍只有 auto + user 两条 title
   事件（未被覆盖/追加），历史读取不变。
9. takeover launcher（真实 CLI）：`takeover-launcher.cmd -V` → `0.2.1-rc.3`
   + 退出 0；未知 profile → `profile "…" does not exist` + 退出 1（证明是 CLI
   而不是桌面 GUI）。
10. 单元测试：`tests/test_dsh_adapter.py` + `tests/test_dsh_sessions.py`（65 项，
    覆盖 NDJSON 解析、spawn argv、resume、final/error、sessions provider、
    zstd 后端选择、标题写入/幂等、fork、takeover launcher）。

未验证（明确缺口）：有凭据时的**模型真实回答**、`tui`/`desktop` profile 的
交互式 takeover 会话本身、SSE 型 MCP、Windows 之外的路径。
