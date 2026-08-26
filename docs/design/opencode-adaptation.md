# OpenCode 适配设计文档

> 目标：以 Pan 在 cbc 上实现的功能全集为基准，补齐 opencode (sst/opencode) 适配，使同一套 Pan 功能（spawn/resume/fork/takeover/enrich/import/MCP）由 opencode 驱动。
> 状态：设计定稿，实现派发中。opencode 版本 `1.18.4`，本地安装 `PATH=/d/node_npm/node_global/opencode`，数据目录 `~/.local/share/opencode/`。
> 日期：2026-08-26

## 1. cbc 功能基准（Pan 在 cbc 上已实现的全部能力）

（与 kimi 文档第 1 节同构，此处仅列能力清单，细节见 `packages/core/worker.py` 与 `adapters/cbc/`。）
stream 长驻 / one-shot MCP / 模型列表 / resume / fork / thinking / effort / 权限模式 / MCP / stdin 编码 / stdout 解析 / takeover / enrich / 可执行路径。
opencode 逐一对照见第 3 节。

## 2. 侦察关键事实（2026-08-26 本地验证 + 官方文档）

### 2.1 进程级接入能力（`opencode --help` / `run --help`）
- `opencode run [message..]`：**一次性进程**，`--format json` 把结构化事件以 JSONL 写到 stdout。
- `run` **无 `--stdio` / 无 `--input-format`**：不支持 cbc 式的 stdin 长驻流式协议。消息以位置参数传入。
- 长驻替代品（非 stdin/stdout 简单流）：
  - `opencode acp`：ACP (Agent Client Protocol) server（默认监听端口，非 stdio 长驻流）。
  - `opencode serve`：headless HTTP server（端口）。
  - `opencode attach <url>`：接入运行中 server。
  - 这些适合后续升级（ACP/serve 长驻），但非本轮简单 wrapper 模式目标。
- `run` 关键参数：
  - `-m/--model <provider/model>`
  - `-s/--session <id>` / `-c/--continue`：恢复会话
  - `--fork`（需配合 `--continue` 或 `--session`）：fork 会话 **再运行**（无独立 headless fork）
  - `--variant <v>`：provider 特定 reasoning effort（示例 `high`/`max`/`minimal`）
  - `--thinking`：在输出中显示 thinking 块
  - `--auto`：自动批准未被显式拒绝的权限（危险！等价于 bypass）—— **唯一权限 CLI flag**
  - `-f/--file`、`--title`、`--agent`、`--dir`、`--command`
- 子命令：`session`（list/delete）、`export <id>`、`import <file>`、`providers`、`models`、`db`、`mcp`。
  - 注意：`session list` / `export` 按**当前 cwd 的项目**过滤，跨目录会话查不到
    （实测 `export ses_07a75...` 从非其根目录运行时返回 "Session not found"）。
    因此 sessions.py **直接读 SQLite**，不依赖这些易变的 CLI 子命令。

### 2.2 `--format json` 事件结构（权威来源：takopi cheatsheet + 本地 error 实测）
`opencode run --format json` 每行一个 JSON 对象（`type` 字段标识事件）：

| `type` | 关键字段 | 含义 |
|---|---|---|
| `step_start` | `sessionID`, `part.{id,messageID,type:"step-start",snapshot}` | 处理步开始 |
| `text` | `sessionID`, `part.{type:"text",text,...}` | 助手文本（assistant 内容） |
| `tool_use` | `sessionID`, `part.{tool,state:{status:"completed",input,output,title}}` | 工具调用（已完成态才下发） |
| `step_finish` | `sessionID`, `part.{reason:"stop"\|"tool-calls",tokens:{input,output,reasoning,cache:{read,write}},cost}` | 处理步结束（含用量） |
| `error` | `sessionID`, `error.{name,data:{message,statusCode,isRetryable}}` | 会话错误 |

本地实测 error 事件（401）：
```json
{"type":"error","timestamp":1787713121731,"sessionID":"ses_fc3fe9dfaffeNR97o208udOwo7",
 "error":{"name":"APIError","data":{"message":"Invalid Authentication","statusCode":401,"isRetryable":false}}}
```
- **`sessionID` 出现在每个事件上**（格式 `ses_XXX`）→ `extract_session_id` 直接取 `event["sessionID"]`。
- **无原生 `result` 事件**：完成由进程退出 / `step_finish reason:"stop"` 表征。
  **决策**：wrapper 在每次 `opencode run` 结束时**合成** `{"role":"result","is_error":...,"result":...}`（对齐 kimi wrapper）。
- **streaming 事件不含 model 字段**（cheatsheet 未列）。model 由两处兜底：① 建会话时用户显式指定；
  ② `enrich_after_result` 从 SQLite `session.model` 回填（同 kimi 策略，见 §4.4）。
- **thinking/reasoning**：DB `part` 表存在 `{"type":"reasoning","text":...}`；streaming 推测为
  `type:"reasoning"` 或 `type:"text"` + `part.type:"reasoning"`。适配器两种都兼容（§4.2）。
- **工具调用**：`tool_use` → 块 `{role:"tool", content:"<tool>(<input_json>)"[ + 输出]}`。

### 2.3 opencode.json 配置（官方文档 opencode.ai/docs/config/）
- 位置：`~/.config/opencode/opencode.json`（或 `.jsonc`）；项目根 `opencode.json` 覆盖全局。
- 段：`model`（默认模型 `provider/model`）、`small_model`、`provider.{name}.options.{apiKey,timeout,...}`、
  `permission`（工具→模式映射，`*` 通配；文档示例值 `ask`/`deny`，无 `yolo`）、
  `mcp.{name}.{type,url,enabled}`（按 server 名）。
- 变量替换：`{env:VAR}` / `{file:path}`。
- 环境变量：`OPENCODE_CONFIG`（自定义配置路径）、`OPENCODE_CONFIG_CONTENT`（**内联覆盖，除 managed 外最高优先**）、
  `OPENCODE_MODEL`。Provider API key 可用 `provider.x.options.apiKey={env:XXX}` 或标准环境变量（如 `ANTHROPIC_API_KEY`）。
- 实测 `~/.config/opencode/opencode.jsonc` 当前仅含 `{"$schema": "https://opencode.ai/config.json"}`，
  无 model/key 配置。

### 2.4 模型列表
- `opencode models` 每行输出一个 `provider/model`（实测 `opencode/...`、`moonshotai/...`、`moonshotai-cn/...`）。
  `supported_models` 解析其输出，回退 config > 内置默认。
- 默认模型未知（help 未给默认）→ 由 config.json `opencode.model` 指定，回退内置 `opencode/deepseek-v4-flash-free`。

### 2.5 权限模式
- `run` 仅 `--auto`（自动批准未显式拒绝项）。无 `--yolo`/`--permission-mode`。
- `permission_modes = [{"value":"","label":"default (config)"}, {"value":"auto","label":"auto (--auto, 绕过 ask)"}]`。

### 2.6 SQLite 存储结构（`~/.local/share/opencode/opencode.db`，事件溯源）
- `session`：`id, parent_id`（fork 父）, `slug, directory, title, model(JSON:{id,providerID,variant}), agent, permission, cost, tokens_input/output/reasoning/cache_read/cache_write, time_created/updated`。
- `message`：`id, session_id, data(JSON:{role, time, agent, model{modelID,providerID}, ...})`。
  - user 消息：`{role:"user", model, summary}`；assistant：`{role:"assistant", tokens, modelID, providerID, finish|error}`。
- `part`：`id, message_id, session_id, data(JSON)` —— 实际内容块：
  - `{"type":"text","text":...}`（用户/助手文本）
  - `{"type":"reasoning","text":...}`（思考）
  - `{"type":"step-start"}` / `{"type":"step-finish","reason":"stop","tokens":...,"cost":...}`
- `session_input`：`prompt` 等。
- **sessions.py 直接读该库**（只读连 `file:...?mode=ro`）实现 list/parse/usage/import；fork 用受控写复制行。

## 3. opencode 对照 cbc 能力差距

| # | 能力 | opencode 现状 | 接入方式 |
|---|---|---|---|
| G1 | stream 长驻 | 无 `--input-format` | **wrapper 模式**：wrapper.py 长驻，`opencode run` 逐条一次性调用（对齐 kimi） |
| G2 | one-shot | `run --format json` 原生 | wrapper 内部每消息 spawn 一次 |
| G3 | resume | `--session <id>` / `--continue` | wrapper 持有并复用 sessionID |
| G4 | fork | `--fork` 需真实 run 才提交 | **DB 行复制** `fork_opencode_session`（headless 可测，对齐 kimi 文件复制） |
| G5 | thinking | `--thinking`（显示） | `thinking_args` → `--thinking`（按 adapter_config.thinking） |
| G6 | effort | `--variant <v>` | `effort_args` → `--variant <v>`（provider 特定） |
| G7 | 权限 | 仅 `--auto` | `permission_mode_args` → `--auto`（mode=="auto"） |
| G8 | MCP | `opencode.json` `mcp` 段，无 `--mcp-config` | 本轮 `mcp_args` 返回 []（见 §4.5，同 kimi 取舍） |
| G9 | stdin 编码 | 无 | wrapper 读 stdin JSON `{"text":...}`（对齐 kimi） |
| G10 | stdout 解析 | JSONL `type` 事件 + 合成 result | `parse_event` + 系列提取；wrapper 合成 `result` |
| G11 | takeover | TUI `opencode --session <id>` | `takeover_command` → `[opencode, "--session", id]` |
| G12 | enrich | SQLite `session.tokens_*`/`cost` | `enrich_after_result` 读 DB 增量（time 游标，对齐 kimi） |
| G13 | 模型列表 | `opencode models` | `supported_models` 解析 |
| G14 | import | SQLite 可直读 | `/api/opencode/sessions` + import（对齐 cbc） |

## 4. 设计决策

### 4.1 接入路径：wrapper 模式（一次性 `run` + 长驻包装）
opencode `run` 无 stdin 长驻协议，故**不能**走 cbc 的 `--input-format stream-json` 长驻路径。
采用与 kimi 同构的 wrapper：`opencode/wrapper.py` 作为 Pan Worker 子进程长驻，内部循环
`opencode run "<text>" --session <id> --format json [--model/--variant/--thinking/--auto]`，
转发 stdout JSONL 并在结束时合成 `result` 事件。Worker stream 模式（`build_spawn_args` 长驻进程 + stdin 写消息）天然复用。

### 4.2 stdout 事件解析（对齐 cheatsheet + DB）
- `is_init_event`：事件含 `sessionID` 即 True（每个事件都有；worker 仅首次写入 `cli_session_id`，幂等安全）。
- `is_assistant_event`：`type` ∈ {`text`, `tool_use`, `reasoning`}。
- `extract_assistant_blocks`：
  - `text` → `{role:"assistant", content: part.text}`
  - `reasoning` 或 `text`+`part.type=="reasoning"` → `{role:"thinking", content: part.text}`
  - `tool_use` → `{role:"tool", content:"<tool>(<input_json>)\n→ <output>"}`
- `is_result_event`：`role=="result"`（合成事件）。`extract_result_text`：`result`。`is_result_error`：`is_error`。
- `extract_model`：返回 None（streaming 无 model）→ enrich 回补。

### 4.3 session 连续性（wrapper 持有 sessionID）
- 首条消息无 sessionID → `opencode run` 自动建会话，wrapper 从事件 `sessionID` 记录。
- 后续消息带 `--session <id>`。
- 恢复既有 Pan 会话：spawn wrapper 时传 `--session-id <existing>`，首条即用 `--session`。

### 4.4 extract_model / enrich（G12，对齐 kimi §4.3/§4.4）
- 数据源：`opencode_sessions.get_raw_usage(session_id)`（SQLite `session.tokens_*`/cost/model →
  `{model, rawUsage, timestamp}`）。
- 增量游标：用 `session.time_updated`（epoch ms）存于 `adapter_config["opencode_last_usage_ts"]`，
  仅返回更新的条目，然后推进游标（对齐 kimi 的 time 游标）。
- model 回补：从 `session.model` JSON 取 `{id,providerID}` → 回填 `s.model`。

### 4.5 MCP（G8）
opencode 无 `--mcp-config`；MCP 来自 `opencode.json` 的 `mcp` 段（用户级/项目级）。
本轮 `mcp_args` 返回 []（同 kimi 取舍 §4.5）——MCP 由用户 opencode.json 配置，不通过 Pan 注入。
后续可写项目级 `opencode.json` 注入（待定）。

### 4.6 supported_settings / 权限 / thinking / effort（G5/G6/G7）
- `supported_settings = ["model","permissionMode","effort","thinking"]`。
- `permission_modes = [{"value":"","label":"default (config)"},{"value":"auto","label":"auto (--auto)"}]`。
- `effort_values = ["","minimal","low","medium","high","max"]`（`--variant`）。
- `thinking`：`adapter_config.get("thinking")` → `--thinking`。

### 4.7 fork（G4）：DB 行复制，非 native `--fork`
native `--fork` 需真实 run 才提交（实测空消息 fork 不建子会话、且 API 401 不提交）。
故 `fork_opencode_session(parent_id, name, workdir)`：在 `opencode.db` 事务内
插入新 `session`（新 `id`、`parent_id=parent`、复制 `model/agent/permission/directory/slug`），
并复制 `message`/`part` 行（`session_id`/id 重映射），形成带历史的可恢复 fork。
headless 可测（无需 API）。**假定**：opencode 从 `session`/`message`/`part` 表恢复会话；
若 opencode 还要求 `event`/`event_sequence` 溯源行，需在实现后复核（见 §6 风险）。

### 4.8 takeover（G11）
`takeover_command` → `[_OPENCODE_PATH, "--session", s.cli_session_id]`（TUI）。

### 4.9 免费模型实测结论（阻塞已解除，2026-08-26 复测）

**结论：opencode 网关免费模型（`opencode/*` 前缀）无需用户 API key 即可正常使用**，网关侧处理鉴权，实测 `cost:0`。原 §4.9 阻塞源于误用了一个**当时不可用**的 free 模型（`deepseek-v4-flash-free`），而非 opencode 适配或免费额度本身有问题。

实测清单（均带 timeout，环境 Windows，`opencode` v1.18.23，路径解析为 `opencode.CMD`）：

| 模型 | `opencode run "1+1" --format json` | 说明 |
|------|------|------|
| `opencode/big-pickle` | ✅ "2" | **可用，已设为默认模型** |
| `opencode/mimo-v2.5-free` | ✅ "2" | 可用 |
| `opencode/nemotron-3-ultra-free` | ✅ "2"（含 reasoning tokens） | 可用 |
| `opencode/deepseek-v4-flash-free` | ❌ 服务端 500 "Unexpected server error" | **不可用**（原默认，已替换） |
| `opencode/north-mini-code-free` | ❌ 401 "Model north-mini-code-free is not supported" | 不可用 |
| `opencode/hy3-free`、`opencode/muse-spark-1.2-contributor-free`、`opencode/nemotron-3.5-lightning-free`、`opencode/x-preview-f-free` | 未逐一 run（列于 `opencode models`，属免费池） | 待按需复测 |

- 注：`gpt-5-mini-free` / `gemini-2.0-flash-free` 不在本网关 `opencode models` 清单内，不选用。
- `opencode models` 解析出的免费池比手写内置列表更大；`supported_models` 优先取 CLI 解析，故前端模型下拉会随网关自动更新。

**默认模型调整**：`adapter.py` `_DEFAULT_MODEL` 与 `config.py` `opencode.model` 均改为 `opencode/big-pickle`（原 `deepseek-v4-flash-free` 不可用）。`_BUILTIN_MODELS` 重排为可用 free 模型在前。

**成功回合端到端复验（独立跑 wrapper，未起服务、不碰 8768/8080/NapCat）**：
1. `opencode/big-pickle` 跑通完整链路：wrapper spawn → 流式 `text` 事件 → 合成 `{"role":"result",...}`。
2. `text`/`reasoning`/`tool_use` 事件解析均验证：plain 回合 `block_roles=['assistant']`、result_text='2'；`--thinking` 回合 `block_roles=['thinking','assistant']`（reasoning 事件正确归 thinking）；`tool_use` 事件归 `role:tool`（craft 事件验证 `Bash(ls)\n→ file.txt`）。
3. `enrich_after_result` 从 SQLite 回填：返回 `model="opencode/big-pickle"` + `rawUsage{prompt_tokens,completion_tokens,cache_read_tokens,cost:0.0}`，并回填 `s.model`。说明 streaming 事件无 model 字段、由 DB 聚合用量回补的假设成立。
4. `MOONSHOT_API_KEY` 仍 401（无效/过期），但 opencode 免费模型链路已完全不依赖它。

- 历史修复记录：wrapper 初版在 stdin EOF 后不退出（主循环阻塞在 `message_queue.get()`），已让 `_stdin_reader` 在 EOF 时入队 `None` 哨兵，主循环收到后退出（仅增强健壮性，不影响运行时 Pan 主动 kill 行为）。

## 5. 任务分解

### T1 适配器实现（opencode worker，hy3）
- [x] 调查：run/--format json/事件结构/config/权限/MCP/SQLite（见本文）
- [x] `packages/core/adapters/opencode/__init__.py`（注册 OpencodeAdapter）
- [x] `packages/core/adapters/opencode/adapter.py`（CliAdapter 协议全量实现）
- [x] `packages/core/adapters/opencode/wrapper.py`（长驻包装，对齐 kimi wrapper）
- [x] `packages/core/adapters/opencode/sessions.py`（SQLite list/parse/usage/fork/import）
- [x] `packages/core/config.py` 增加 `opencode` 配置段
- [x] `packages/core/adapters/__init__.py` 注册 opencode
- [x] `packages/web/server.py`：branch/rename 增加 opencode 分支 + `/api/opencode/sessions`(import)（对齐 cbc，**追加不覆盖 kimi**）
- [x] 前端：supportedSettings 通用驱动，确认无特改

### T2 测试验证（独立端口，不碰运行中服务）
- [x] 静态：import OpencodeAdapter，协议方法齐备（`python -c` 校验）
- [x] wrapper 管道：spawn wrapper → 写 stdin 消息 → 收合成 result(is_error=true)（验证 error 事件解析，已端到端实测）
- [x] sessions.py：list/parse/usage/fork 直读 SQLite 验证（headless）
- [x] 成功回合解析：已用 `opencode/big-pickle` 免费模型端到端复测通过（见 §4.9）

### T3 提交
- [ ] `git add` + `commit`（feat(opencode): ...）
- [ ] 报告（经 Pan 订阅自动送达，无需 SendMessage）

## 6. 风险与边界
- 成功事件解析已用 `opencode/big-pickle` 免费模型经真实成功回合验证（`text`/`reasoning`/`tool_use` 解析 + SQLite enrich 回填均通过，见 §4.9）。
- fork 经 DB 复制，假定 opencode 从 `session/message/part` 恢复；若还需 `event` 溯源行，需补。
- 仅改本 worktree（feat/cli-adapters）；不动 D:/project/Pan；不碰 8768/8080/NapCat；测试用 8793/8794。
- 共享文件（server.py / config.py / __init__.py）编辑前必 `git status`/`diff`，保留 kimi 改动，仅追加 opencode。
