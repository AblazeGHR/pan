# CLI Adapter 特殊行为与踩坑参考

> Pan 各 CLI adapter（cbc / kimi / opencode / claude / codex）实战踩过的特殊行为与坑。
> 每条 = 现象 + 根因 + 处理/规避 + 关联代码位置，供后续开发与排障直接查用，不必重复踩坑。
>
> 整理：2026-08-27。
> 说明：claude / codex adapter 最初在独立分支（feat/claude-adapter、feat/codex-adapter）开发，
> opencode 的「模型 TTL 缓存 + 多段模型名」在 feat/opencode-models（5787937）开发——
> **这些分支均已合入 main（2026-08-27 核对）**，本文描述即 main 现状。

## 目录（各 CLI × 行为索引）

> 行为条目按「现象 / 根因 / 处理与规避 / 代码位置」四段展开，见对应章节号。

| 章节 | CLI | 行为 |
|---|---|---|
| §1.1 | 全部 | npm `.CMD` shim → 中文参数乱码（Windows） |
| §1.2 | 全部 | wrapper 子进程 `stdin=DEVNULL`（防 EOF 挂起） |
| §1.3 | 全部 | wrapper stdin 二进制读 + UTF-8 显式解码 |
| §2.1 | cbc | `execution_modes = ["stream","oneshot"]` |
| §2.2 | cbc | `.CMD` shim → `node <codebuddy.js>` |
| §2.3 | cbc | 模型解析：`cbc --help` |
| §2.4 | cbc | MCP 必须显式 `--mcp-config`（`-d` 不自动发现、项目级 `.mcp.json` 会阻塞） |
| §2.5 | cbc | `-d` 冗余（cwd 派生项目目录） |
| §2.6 | cbc | oneshot 跳过 `thinking_args`（`--settings` 破坏 MCP init） |
| §2.7 | cbc | takeover 用 node 入口 + cwd=workdir |
| §2.8 | cbc | enrich 从 JSONL 读新增 rawUsage |
| §2.9 | cbc | 项目目录 sanitize / 反向解析 |
| §3.1 | kimi | `execution_modes=["stream"]`（wrapper 长驻） |
| §3.2 | kimi | 可执行路径（Windows 下 `~/.kimi-code/bin/kimi.exe`） |
| §3.3 | kimi | 模型解析：`~/.kimi-code/config.toml` |
| §3.4 | kimi | MCP：folder-trust 门禁 → 隔离 HOME（KIMI_CODE_HOME） |
| §3.5 | kimi | `-p` 与 `-y/--auto/--plan` 互斥；thinking/effort 无 CLI 参数 |
| §3.6 | kimi | resume：`-S` 恢复上下文但不重放历史事件 |
| §3.7 | kimi | stdout 事件无 model 字段 → enrich 回填 |
| §3.8 | kimi | enrich：wire.jsonl `usage.record` 时间游标 |
| §3.9 | kimi | fork：目录复制 |
| §4.1 | opencode | `execution_modes=["stream"]`（wrapper 长驻） |
| §4.2 | opencode | `.CMD` shim → `bin/opencode.exe` |
| §4.3 | opencode | 模型解析：`opencode models` 多段名 + TTL 缓存 |
| §4.4 | opencode | MCP：项目级 `opencode.json(c)`（git 根定位 + 合并写） |
| §4.5 | opencode | 事件：sessionID 每事件携带 / result 由 wrapper 合成 |
| §4.6 | opencode | stdout 无 model 字段 → enrich 从 SQLite 回填 |
| §4.7 | opencode | enrich：SQLite session 表聚合用量增量 diff |
| §4.8 | opencode | fork：DB 行复制 |
| §4.9 | opencode | takeover：顶层 `opencode --session <id>`（非 run） |
| §5.1 | claude | `execution_modes=["oneshot"]`（`claude -p` 一次性） |
| §5.2 | claude | `.CMD` shim → `bin/claude.exe` 或 `node cli.js` |
| §5.3 | claude | 模型：无 CLI 列表，仅内置白名单 |
| §5.4 | claude | MCP：`--mcp-config`（与 cbc 同格式） |
| §5.5 | claude | 事件格式与 cbc 同构；thinking 自动产出 |
| §5.6 | claude | oneshot 用量不落账 + cost 权威来源在 stdout result |
| §5.7 | claude | JSONL 无 result 事件（cost 不在 JSONL） |
| §5.8 | claude | fork：JSONL 复制 |
| §5.9 | claude | 项目目录编码 `~/.claude/projects/<encoded-cwd>` |
| §6.1 | codex | `execution_modes=["stream"]`（wrapper 长驻） |
| §6.2 | codex | `.CMD` shim → `node codex.js` |
| §6.3 | codex | 模型：`models_cache.json` > 白名单 > model_catalog_json |
| §6.4 | codex | MCP 内联注入 + developer instructions 注入 |
| §6.5 | codex | 权限模式映射；`-c` 覆盖可随 resume 生效 |
| §6.6 | codex | thread cwd 归一化为 git 根；Pan 用祖先匹配兼容 |
| §6.7 | codex | fork 走 DB 行复制；首次 resume 已通过本机 e2e |
| §6.8 | codex | **遗留** 事件命名 snake_case vs camelCase |
| §6.9 | codex | resume 只透传 `-c` 类覆盖（丢弃一次性 flag 与 `-C`） |
| §6.10 | codex | `--skip-git-repo-check` |
| §6.11 | codex | enrich：rollout JSONL `token_count` 聚合增量 |
| §7 | cbc/claude | **通用** oneshot 路径不调 `enrich_after_result` → 用量不落账 |

---

## 0. 背景与约定

- **协议层**：`CliAdapter`（`packages/core/adapters/base.py`）定义统一接口；各 CLI 实现类
  （`packages/core/adapters/<cli>/adapter.py`）提供 argv 拼装 / 事件解析 / enrich；
  `sessions.py` 提供原生 session 存储读写；wrapper 型 adapter 另有 `wrapper.py`。
- **执行模式**：`execution_modes` 是「worker 驱动 adapter 的方式」，`["stream"]` = worker 起一个
  长驻进程跨消息复用 stdin/stdout；`["oneshot"]` = worker 逐任务 spawn 短进程、prompt 作末参
  （详见 `docs/design/adapter-p1-oneshot.md`）。wrapper 型 adapter 内部对 CLI 的一次性调用对
  worker 透明，不暴露 oneshot。
- **enrich**：`enrich_after_result(s)` 在每轮结果后从 CLI 原生存储补记 token/credit 用量。

---

## 1. 跨 CLI 通用坑

### 1.1 npm `.CMD` shim 中文参数乱码（Windows）

- **现象**：Windows 下 `shutil.which("xxx")` 返回 npm 生成的 `.CMD`/`.bat` shim。把它直接交给
  `subprocess` / `asyncio.create_subprocess_exec` 时，进程经 `cmd.exe /c` 启动；`cmd.exe` 用系统
  ANSI 代码页（如 GBK/cp936）重新切分命令行，导致**非 ASCII 参数（中文 prompt）乱码**、长/多行
  参数被破坏。CLI 收到乱码 prompt 后表现为：无 stdout 输出、worker 读取超时、会话卡 running，
  或 cbc 在 ~30ms 内退出。
- **根因**：`CreateProcess` 不能直接执行 `.CMD`（批处理），必须经 `cmd.exe` 二次切分；切分按
  ANSI 代码页，与 UTF-8 参数冲突。
- **处理/规避**：把 shim 解析为**真实入口**，参数经 `CreateProcess` 原样传递：
  - cbc → `[node, <dir>/node_modules/@tencent-ai/codebuddy-code|codebuddy/bin/codebuddy[.js]]`
  - opencode → `<dir>/node_modules/opencode-ai/bin/opencode.exe`（原生二进制，直接 exe）
  - claude → `<dir>/node_modules/@anthropic-ai/claude-code/bin/claude.exe`，或回退 `[node, cli.js]`
  - codex → `[node, <dir>/node_modules/@openai/codex/bin/codex.js]`
  - 均支持环境变量覆盖（`PAN_CBC_PATH` / `PAN_OPENCODE_PATH` / `PAN_CLAUDE_PATH` /
    `PAN_CODEX_PATH`）。
- **代码位置**：
  - cbc：`cbc/adapter.py` `_resolve_cbc_argv`（含 shim 同目录 `node.exe` + glob 兜底）
  - opencode：`opencode/adapter.py` `_resolve_opencode_path` / `_resolve_opencode_exe_from_shim`
  - claude：`claude/adapter.py` `_resolve_claude_argv` / `_resolve_claude_exe_from_shim`
  - codex：`codex/adapter.py` `_resolve_codex_js` / `_codex_js_from_shim` / `_resolve_codex_node`

### 1.2 wrapper 子进程 `stdin=DEVNULL`（防 EOF 挂起）

- **现象**：opencode/codex 这类「一次性命令 + 内部循环」的 wrapper，子进程会**读 stdin 等 EOF**；
  若继承 wrapper 的 stdin（来自 server 的长驻管道且保持打开），子进程静默挂起——无任何
  stdout/stderr，表现为会话卡 running、60s 超时、takeover 报 "no CLI session yet"。
- **根因**：prompt 已作为 CLI 参数传入，但 CLI 仍尝试从 stdin 读输入直到 EOF。
- **处理/规避**：wrapper 内 `subprocess.Popen(..., stdin=subprocess.DEVNULL, close_fds=True)`
  切断与 server 管道的连接；`close_fds` 避免继承 server 的其它句柄（监听 socket 等）。
- **代码位置**：`opencode/wrapper.py` `_main_loop`（Popen）、`codex/wrapper.py` `_main_loop`。
  - ⚠️ **注意**：`kimi/wrapper.py` **未**设 `stdin=DEVNULL`（kimi `-p` 实测未出现挂起，但属潜在
    风险点——若 kimi 未来版本开始读 stdin，需同样处理）。

### 1.3 wrapper stdin 二进制读 + UTF-8 显式解码

- **现象**：Windows 下 `TextIOWrapper` 用系统 locale 编码（如 cp936）解码 UTF-8 字节，导致中文
  乱码、`json.loads` 失败、消息被静默丢弃。
- **处理**：wrapper 后台线程用二进制读 `sys.stdin.buffer` 并按 UTF-8 显式解码
  （`_stdin_reader`）；stdout 统一用 `sys.stdout.buffer.write(text.encode("utf-8"))` 输出。
- **代码位置**：`opencode/wrapper.py`、`codex/wrapper.py`（kimi 用 `sys.stdin.readline`，未显式
  二进制解码——同属观察项）。

---

## 2. cbc（CodeBuddy CLI）

### 2.1 execution_modes = `["stream", "oneshot"]`

- **现象**：cbc 同时支持 stream 长驻（原生 stdin/stdout，`-p --output-format stream-json
  --input-format stream-json`）与 oneshot 逐任务短进程（prompt 作末参 + `--mcp-config`）。
- **处理**：`execution_modes = ["stream", "oneshot"]`；worker 按 `resolve_execution_mode` 分派
  （`packages/core/adapters/resolution.py`）。仅 cbc 暴露 `oneshot`（worker 直接 spawn 短进程）；
  kimi/opencode/codex 内部的一次性对 worker 透明。
- **代码位置**：`cbc/adapter.py` `execution_modes`、`oneshot_args`、`base_args`/`base_args_stream`。

### 2.2 `.CMD` shim 解析为 `node <codebuddy.js>`

见 §1.1。cbc 的 shim 布局在
`<dir>/node_modules/@tencent-ai/codebuddy-code|codebuddy/bin/codebuddy[.js]`，命中返回
`[node_exe, entry]`（shim 同目录优先用 `node.exe`）。**给 `_parse_models_from_cbc_help` 必须传
node 解析后的 argv**，裸 `["cbc"]` 在 Windows 上会 FileNotFoundError。

### 2.3 模型解析：`cbc --help`

- **现象**：需要可用模型列表；无 config 时靠 CLI 自报。
- **处理**：优先级 `config.json("cbc".models) > cbc --help 解析 > 内置白名单
  _BUILTIN_MODELS`。`cbc --help` 输出里正则
  `Currently supported:\s*\(([^)]+)\)` 提取逗号分隔模型名。**class 级缓存**（`_cached_models`），
  仅进程生命周期内生效。
- **代码位置**：`cbc/adapter.py` `_parse_models_from_cbc_help`、`supported_models`。

### 2.4 MCP：`--mcp-config` 显式传（`-d` 不自动发现）

- **现象**：MCP server 连不上（工具不可见也搜不到），或出现 "Needs approval"/"Failed to connect"。
- **根因**（2026-08-16 实测，cbc 2.136.0）：
  - `-d` **不会**自动发现 `.codebuddy/mcp.json` → MCP 未连接；
  - workdir 内项目级 `<workdir>/.mcp.json` 会被发现为 project-scope MCP server，**没有 `-d` 时该
    注册反而阻塞 `--mcp-config`**（注册与显式配置冲突）。
- **处理**：只有显式 `--mcp-config <path>` 才让 MCP 连接；配置文件收敛到 Pan 自己的
  `data/mcp-configs/<session_id>.mcp.json`（`MCP_CONFIG_DIR = SESSION_DIR.parent / "mcp-configs"`），
  **绝不写 workdir**（workdir 可能在 Pan 外，写外部目录污染且可能不可写）。描述符构造与 pan/pan-qq
  身份注入由 `adapters/mcp.py` 共享 helper 收敛。
- **代码位置**：`cbc/adapter.py` `mcp_args`、`MCP_CONFIG_DIR`；`adapters/mcp.py`
  `build_mcp_servers`/`write_mcp_json`。详见 `docs/references/cbc-mcp-defer-机制.md`。

### 2.5 `-d` 冗余（cwd 派生项目目录）

- **现象**：`build_spawn_args` 不传 `-d`。
- **根因**：cbc 从**进程 CWD**（`create_subprocess_exec(cwd=s.workdir)`）派生项目目录，同时决定
  JSONL 存储位置；`-d` 只对旧的 `enableAllProjectMcpServers` 发现有意义，`--mcp-config` 已替代
  （2026-08-16 实测 `-d` 对 connect/resume 冗余）。
- **代码位置**：`cbc/adapter.py` `build_spawn_args` 注释。

### 2.6 oneshot 跳过 `thinking_args`

- **现象**：oneshot（MCP）路径不能传 `--settings`。
- **根因**：`--settings`（`{"alwaysThinkingEnabled": false}`）会破坏 MCP init；旧 `_consumer_mcp`
  同样跳过。
- **处理**：`oneshot_args` 只拼 model/permission/effort/resume/mcp，**不拼 thinking_args**；
  `--system-prompt` 仅首条（`cli_session_id` 捕获前）注入，之后靠 `--resume` 延续。
- **代码位置**：`cbc/adapter.py` `oneshot_args`。

### 2.7 takeover 命令细节

- **现象/处理**：
  - 用 **node 解析入口**（非 `.CMD`，PowerShell/cmd 会乱码）；
  - 终端以 `cwd=<workdir>` 打开（`_open_terminal`），cbc 从 CWD 派生项目目录——**传 `-d` 反而会
    在 CWD 不同时破坏 resume**（JSONL 在 CWD 派生项目下）；
  - **不重注入 `--system-prompt`**（takeover 只 resume 既有会话，system prompt 由 cbc 原生 JSONL
    承载；重注入会把它当一条 user 消息重复）。
- **代码位置**：`cbc/adapter.py` `takeover_command`。

### 2.8 enrich：JSONL 增量读取

- **现象/处理**：从 `~/.codebuddy/projects/` 的 session JSONL 读取本轮新增的 `rawUsage`。不读尾部
  16KB，而是**读全文件 + 用 per-model `request_count` 作已累积游标**只返回新增条目；先 `sleep(0.2)`
  等 cbc 完成 JSONL 写入（时序竞态）。也扫描子 agent 的 `agent-*.jsonl`（子 agent 独立耗额度）。
- **代码位置**：`cbc/adapter.py` `enrich_after_result` / `_read_jsonl_new_entries`、
  `cbc/sessions.py` `get_raw_usage`。

### 2.9 项目目录 sanitize / 反向解析

- **现象**：cbc 把路径 sanitize 为目录名，`D:\project\CLIConductor` → `d-project-CLIConductor`
  （去盘符冒号、小写、`\`/`/`→`-`、折叠连续 `-`、去首尾 `-`）。路径含 `-` 时该映射有损。
- **处理**：反向解析靠**读该目录任一 JSONL 首事件的 `cwd` 字段**（cbc 每个事件都带 cwd），
  缓存于 LRU；heuristic 兜底仅当无 JSONL。
- **代码位置**：`cbc/sessions.py` `sanitize_project_dir_name` / `_read_project_cwd` /
  `_project_dir_to_path` / `_resolve_session_file`。

---

## 3. kimi（Kimi Code CLI）

### 3.1 execution_modes = `["stream"]`（wrapper 长驻）

- **现象/处理**：kimi 没有 stdin 长驻 stream 协议，`-p/--prompt` 是一次性进程。用 `wrapper.py`
  包装为长驻子进程，wrapper 内部循环逐条调 `kimi -p <text> --output-format stream-json`
  （续接 `-S <id>`），转发事件并在每次调用结束合成一条 `result` 事件。`oneshot_args` 返回 `[]`
  （防御兜底）。
- **代码位置**：`kimi/adapter.py` `execution_modes`、`base_args`；`kimi/wrapper.py`。

### 3.2 可执行路径

- **处理**：Windows 下用绝对路径 `~/.kimi-code/bin/kimi.exe`（PATH 不一定有裸 `kimi`）；环境变量
  `PAN_KIMI_PATH` / `CLICONDUCTOR_KIMI_PATH` 覆盖。kimi 是原生二进制，非 npm shim。
- **代码位置**：`kimi/adapter.py` `_KIMI_PATH`。

### 3.3 模型解析：`~/.kimi-code/config.toml`

- **处理**：优先级 `config.json("kimi".models) > ~/.kimi-code/config.toml 的 [models."..."] 段 >
  内置白名单`。`config.json` 里的 model 可能是历史遗留无效值（`kimi-code/kimi-for-coding` 已不在
  可选模型内），仅当它在可选列表内才采用。
- **代码位置**：`kimi/adapter.py` `_parse_kimi_models_from_toml`、`default_model`。

### 3.4 MCP：folder-trust → 隔离 HOME

- **现象**：kimi **无 `--mcp-config` 参数**；项目级 mcp.json 受 folder-trust 门禁拦截——非交互
  `-p` 无法应答信任提示，实测项目级 MCP 不注册。
- **处理（方案 C）**：在 `data/kimi-homes/<session_id>/` 准备**隔离 HOME**（拷贝真实
  `~/.kimi-code/config.toml`（含 api_key，只读不回写）+ 写 `mcp.json`），经 wrapper 以
  `KIMI_CODE_HOME` 环境变量注入 kimi 子进程。隔离 HOME 对 kimi 而言即「用户级」，天然绕过信任
  门禁。`mcp_args` 返回 wrapper 的 `--kimi-home` 参数（kimi 本身无 MCP CLI flag）。
- **代码位置**：`kimi/adapter.py` `mcp_args` / `_prepare_kimi_home`；`kimi/wrapper.py`
  `env["KIMI_CODE_HOME"]`。设计：`docs/design/kimi-mcp-solution.md`。

### 3.5 `-p` 与权限参数互斥；thinking/effort 无 CLI 参数

- **现象**：实测（2026-08-25）`-p` 与 `-y`/`--auto`/`--plan` 互斥——kimi 直接报
  "Cannot combine --prompt with --yolo/--auto/--plan"；thinking/effort 无独立 CLI 参数（在
  config.toml 全局配置）。
- **处理**：`permission_mode_args` / `thinking_args` / `effort_args` 返回 `[]`；
  `supported_settings = ["model"]`（前端只展示 model）。
- **代码位置**：`kimi/adapter.py` 各 args 方法、`supported_settings`。

### 3.6 resume：`-S` 恢复上下文但不重放历史

- **现象**：`kimi -S <id>` 恢复对话上下文但**不重放历史事件**（与 cbc `--resume` 差异：cbc 续写
  JSONL 且 worker 在 oneshot MCP 路径 resume；kimi 仅恢复上下文，无 init 事件回放）。
- **处理**：语义等价 cbc resume，故 `supports_resume = True` 让 worker 在 oneshot MCP 路径 resume，
  stream 路径由 wrapper 自己 `-S`。
- **代码位置**：`kimi/adapter.py` `resume_args`、`supports_resume` 注释。

### 3.7 stdout 无 model 字段 → enrich 回填

- **现象**：kimi stream-json stdout 事件只有 meta(system.version) / assistant /
  meta(session.resume_hint)，均不含 model/modelAlias 字段。
- **处理**：`extract_model` 固定返回 `None`；model 由两处兜底——① session 创建时用户显式指定；
  ② enrich 时从 `usage.record` 的 model 回填。
- **代码位置**：`kimi/adapter.py` `extract_model`、`_read_kimi_new_entries`。

### 3.8 enrich：wire.jsonl `usage.record` 时间游标

- **处理**：从 kimi 原生 `wire.jsonl`（`<session_dir>/agents/main/wire.jsonl`）读
  `usage.record` 事件；用其自带的递增 `time`（epoch ms）作增量游标
  （`adapter_config["kimi_last_usage_ts"]`），只返回游标后的条目并推进；先 `sleep(0.3)` 等写入。
- **代码位置**：`kimi/adapter.py` `_read_kimi_new_entries`；`kimi/sessions.py` `get_raw_usage`。

### 3.9 fork：目录复制

- **处理**：kimi 无稳定 `--fork` flag。直接复制 session 目录（更新 state.json + 重指向 agent
  homedir），并在 `~/.kimi-code/session_index.jsonl`（或隔离 HOME）注册新 session；新 id 写入
  `s.cli_session_id`，之后 `-S` resume。
- **代码位置**：`kimi/sessions.py` `fork_kimi_session`。

---

## 4. opencode（sst/opencode）

### 4.1 execution_modes = `["stream"]`（wrapper 长驻）

- **现象/处理**：`opencode run` 是一次性进程（无 stdin 长驻协议）。用 wrapper.py 长驻，内部逐条
  `opencode run "<text>" --format json --no-replay [--session <id>]`，转发事件、合成 result。
- **代码位置**：`opencode/adapter.py` `execution_modes`；`opencode/wrapper.py`。

### 4.2 `.CMD` shim 解析为 `bin/opencode.exe`

见 §1.1。opencode 是**原生二进制**，直接返回
`<dir>/node_modules/opencode-ai/bin/opencode.exe`，无需 `node <entry>`。

### 4.3 模型解析：`opencode models` 多段名 + TTL

- **现象**：`opencode models` 输出每行一个模型，形态 `provider[/org][/region/...]/model`，任意
  段数（两段 `provider/model`、三段 `provider/org/model`、四段 `provider/region/org/model` 均合法）。
- **处理**：优先级 `config.json("opencode".models) > opencode models 解析 > 内置白名单`。
  解析正则 `^[\w.\-]+(?:/[\w.\-]+)+$`（段内字符为字母数字、`.`、`-`、`_`），跳过 provider 分组标题。
  内置白名单标注实测可用/不可用（`opencode/deepseek-v4-flash-free` gateway 500、
  `opencode/north-mini-code-free` 401 均不可用）。
- **缓存**：TTL 缓存（`_MODEL_CACHE_TTL = 300.0`，5 分钟，`time.monotonic()` 判断过期，
  config 白名单同样走 TTL；commit 5787937 已合入 main，取代早期 class 级永久缓存）。
- **代码位置**：`opencode/adapter.py` `_parse_models_from_opencode`、`supported_models`。

### 4.4 MCP：项目级 `opencode.json(c)`

- **现象**：opencode **无 `--mcp-config`**；MCP server 来自项目级 opencode.json 的 `mcp` 段，
  `opencode run` 以 cwd==workdir 启动时自动加载。
- **处理**：`mcp_args` 返回 `[]`（无 CLI flag），但顺带把 session 的 `mcp_servers` 写入项目配置：
  - **项目配置定位**：从 workdir 向上找**最近的 `.git` 根**作为项目配置位置；非 git 目录直接用
    cwd（`_opencode_project_config_path`）。沿用已存在的 `.jsonc` 扩展名，否则 `.json`。
  - **合并而非覆盖**：读已存在的 opencode.json(c)，仅更新 `mcp` 段保留其它键；JSONC（`//` 注释）
    容错解析，解析失败备份后重写（best-effort）。
  - **描述符映射**：stdio/local → `{"type":"local","command":[...],"enabled":true}`；remote/http/sse
    → `{"type":..., "url":..., "enabled":true}`；透传 `PAN_API_URL`。
  - **信任边界**：opencode 不像 kimi 需 trust 提示——实测项目级 mcp 在 run 模式直接加载，无交互
    授权阻塞。
- **代码位置**：`opencode/adapter.py` `mcp_args` / `write_opencode_mcp_json` /
  `_opencode_project_config_path` / `_load_opencode_config`。

### 4.5 事件模型：sessionID 每事件携带 / result 由 wrapper 合成

- **现象**：opencode `--format json` streaming 事件**每个都带 `sessionID`**（无独立 init 事件）；
  原生**无 result 事件**（完成由进程退出表征）。
- **处理**：`is_init_event = bool(event.get("sessionID"))`（worker 仅首次写入 cli_session_id，幂等
  安全）；`is_result_event` 认 wrapper 合成的 `{"role":"result"}`。
- **代码位置**：`opencode/adapter.py` `is_init_event` / `is_result_event`；`opencode/wrapper.py`
  `_main_loop`。

### 4.6 stdout 无 model 字段 → enrich 从 SQLite 回填

- **处理**：`extract_model` 返回 `None`；enrich 时用 SQLite `session.model`（JSON 列，含
  providerID/modelID/variant）拼 `provider/model` 回填 `s.model`。
- **代码位置**：`opencode/adapter.py` `extract_model` / `enrich_after_result`；
  `opencode/sessions.py` `_model_str`。

### 4.7 enrich：SQLite 聚合用量增量 diff

- **现象**：opencode `session` 表只存**会话级聚合**用量（非逐轮明细）。
- **处理**：保存上次聚合快照（`adapter_config["opencode_prev_usage"]`），本次返回二者差值作为新增
  条目（各 token 字段 max(0, cur-prev)）。
- **代码位置**：`opencode/adapter.py` `enrich_after_result`；`opencode/sessions.py`
  `get_raw_usage`（列：tokens_input/output/reasoning/cache_read/cache_write/cost）。

### 4.8 fork：DB 行复制

- **处理**：opencode 无 headless `--fork`（fork 需要真实 run commit + API key）。直接复制
  `session/message/part` 三表行到新 session id（`parent_id` 指向父），message 与 part 重新生成 id
  并重映射 `message_id`。`fork_args` 返回 `[]`（由 server branch 端点经 provider 完成）。
- **代码位置**：`opencode/sessions.py` `fork_opencode_session`。

### 4.9 takeover：顶层 `--session`（而非 run）

- **现象/处理**：接管 = 交互式 TUI 续接会话，用**顶层** `opencode --session <id>`（见
  `opencode --help`：`opencode [project]` 默认启动 TUI，`--session` 为顶层选项）。**不要加 `run`
  子命令**——`opencode run` 是一次性非交互执行，会忽略后续交互。
- **代码位置**：`opencode/adapter.py` `takeover_command`。

### 4.10 其它

- **存储**：SQLite 位于 `~/.local/share/opencode/opencode.db`。不用 `opencode session list` /
  `opencode export`——这些子命令限定当前 cwd 项目，跨目录会话返回 "Session not found"
  （2026-08-26 实测）；直接读 DB 权威且目录无关。
- **wrapper 子进程 stdin**：`stdin=DEVNULL`（见 §1.2）。

---

## 5. claude（Claude Code CLI）

### 5.1 execution_modes = `["oneshot"]`（claude `-p` 一次性）

- **现象/处理**：claude `-p`（print 非交互）模式是一次性进程：整段回复以
  `--output-format stream-json` 事件流打到 stdout，最后一条 `result` 事件后退出。**不需要 wrapper**
  ——每条消息 spawn 一个 `claude -p --output-format stream-json --verbose "<prompt>"`，上下文续接用
  `--resume <cli_session_id>`，worker 走通用 `_consumer_oneshot`。天然规避 wrapper 的 stdin EOF
  挂起坑，也无需维护长驻进程。
- **代码位置**：`claude/adapter.py` `execution_modes`、`oneshot_args`、`base_args`。

### 5.2 `.CMD` shim 解析

见 §1.1。claude 本 npm 包是编译二进制，优先命中
`bin/claude.exe`；若安装形态为 node 脚本则回退 `[node, cli.js]`（`_resolve_claude_exe_from_shim`）。

### 5.3 模型：无 CLI 列表，仅内置白名单

- **现象/处理**：claude 没有稳定可解析的 `--list-models` / `--help` 模型清单（不像 cbc 的
  "Currently supported" 段），故不跑 CLI 解析，仅用 `config.json("claude".models)` 显式配置或内置
  `_BUILTIN_MODELS`（`claude-opus-4-8` / `claude-sonnet-4-5` / `claude-haiku-4-5` 全名 +
  `opus/sonnet/haiku` 简写）。`_DEFAULT_MODEL = ""` → 不传 `--model`，让 claude 用其配置默认模型。
- **代码位置**：`claude/adapter.py` `_BUILTIN_MODELS`、`supported_models`、`model_args`。

### 5.4 MCP：`--mcp-config`

- **处理**：claude 支持 `--mcp-config <path>`（JSON，含 `mcpServers` 键，与 cbc 同格式；共享
  helper 写 `data/mcp-configs/<session_id>.mcp.json`）。未配置/写失败返回 `[]`。
- **代码位置**：`claude/adapter.py` `mcp_args`。

### 5.5 事件格式与 cbc 同构；thinking 自动产出

- **现象/处理**：claude stream-json 事件格式与 cbc 几乎同构：
  `{"type":"system","subtype":"init",...}` / `{"type":"assistant","message":{"content":[...]}}` /
  `{"type":"result","is_error":...}`。thinking 在 `-p + --verbose` 下由模型自动产出（stream-json 含
  thinking 块），**无独立 `--thinking` 开关**。
- **代码位置**：`claude/adapter.py` `is_init_event` / `extract_assistant_blocks` /
  `thinking_args`（返回 `[]`）。

### 5.6 oneshot 用量不落账 + cost 权威来源在 stdout result

- **现象**：worker 的 `_consumer_oneshot` **不调用 `enrich_after_result`**（对比 stream 路径
  `_read_stdout` 在 result 处理时调用）→ cbc oneshot 与 claude 的用量不落账（详见 §7）。claude
  的 **cost 唯一权威来源是 stdout 的 result 事件**（JSONL 不含 cost）——enrich 优先从
  `_PENDING_RESULT_USAGE` 模块缓存取
  （`extract_result_text` 解析 result 事件时暂存 usage+cost，按 session_id 键，读取即弹出），
  缓存未命中（如 re-import 路径不触发 extract）则回退读 JSONL assistant 事件 usage（token 准确，
  cost=0）。
- **代码位置**：`claude/adapter.py` `extract_result_text` / `enrich_after_result` /
  `_PENDING_RESULT_USAGE` / `_read_claude_jsonl_usage`。

### 5.7 JSONL 无 result 事件（cost 不在 JSONL）

- **现象**：claude JSONL 只含 user/assistant/tool_result/ai-title 等事件，**不含 result 事件**；
  `total_cost_usd` 只出现在 `claude -p` 的 stdout result 事件上。
- **处理**：cost 只能靠 stdout result 事件捕获（经 enrich 落账）；JSONL 兜底只给 token（cost=0）。
- **代码位置**：`claude/sessions.py` 模块 docstring、`get_raw_usage`。

### 5.8 fork：JSONL 复制

- **处理**：claude 无原生 `--fork`（只在真实 run commit 时 resume）。把 `<session_id>.jsonl` 复制
  成新 UUID 文件名（同项目目录），写 `ai-title` 事件记录 fork 标题；之后 `claude -p --resume
  <new_id>` 加载复制历史。
- **代码位置**：`claude/sessions.py` `fork_session`。

### 5.9 项目目录编码

- **现象**：claude 把 cwd 编码为项目目录名，`C:\Users\x\AppData\Local\Temp\claude-probe` →
  `C--Users-x-AppData-Local-Temp-claude-probe`：`:`、`\`、`/` 全部替换为 `-`；**路径字面 `-`
  保留**（它不是分隔符）。
- **代码位置**：`claude/sessions.py` `_encode_cwd` / `_decode_cwd`。

---

## 6. codex（OpenAI Codex CLI）

> 以下记录 Codex CLI 与 Pan wrapper 的特殊行为、已落地的兼容处理，以及仍待实测的遗留问题。
> 基础 adapter 改造已提交到 `feature/codex-models`；原生 app-server 体验增强继续在该工作树推进。

### 6.1 execution_modes = `["stream"]`（原生 app-server 桥接）

- **现象/处理**：`codex exec` 是一次性命令，虽然可以输出 JSONL，但每轮重启 CLI，无法提供原生
  thread/turn 生命周期与细粒度 delta。Pan 仍保留 `wrapper.py` 作为稳定入口，但默认经
  `--app-server` 启动一个长驻 `codex app-server --stdio`，用 `thread/start`/`thread/resume` 建立上下文，
  用 `turn/start` 驱动后续消息，并将原生通知翻译为 Pan 的事件模型；每轮仅合成一条兼容的 `result`。
  `oneshot_args` 返回 `[]`。
- **收益**：多轮不再逐轮 spawn `codex exec`，原生 `item/*/delta` 可实时转发；app-server 的 thread/turn
  也让 resume、effort 和中断语义更贴近 Codex 原生客户端。
- **代码位置**：`codex/adapter.py` `execution_modes` / `base_args`；`codex/wrapper.py` 稳定入口；
  `codex/app_server_wrapper.py` 协议桥。

### 6.2 `.CMD` shim 解析为 `node codex.js`

见 §1.1。codex 是 node 脚本，解析为
`[node, <dir>/node_modules/@openai/codex/bin/codex.js]`；node 取 shim 同目录 `node.exe` 或 PATH 上
的 `node`（npm global 通常不内嵌 node.exe）。env 覆盖 `PAN_CODEX_PATH` / `PAN_CODEX_NODE`。

### 6.3 模型：使用 Codex 动态模型缓存

Codex 没有稳定公开的 `models` 子命令。Pan 优先读取 Codex CLI 自己维护的
`$CODEX_HOME/models_cache.json`（默认 `~/.codex/models_cache.json`），只展示
`visibility=list` 的模型；缓存不可用时再回退到 `model_catalog_json` 和默认模型。
模型目录中的 `supported_reasoning_levels` 也用于补充 `xhigh`、`max`、`ultra`
等 effort 选项。显式配置的 `codex.models` 白名单始终优先。

- **现象**：codex CLI 无稳定 `models` 子命令（help 未列出）→ 不跑 CLI 解析。
- **处理**：`config.json("codex".models)` > 单默认模型。`default_model` 自动识别：config.json
  model > 读 `~/.codex/config.toml` 的 `model` 字段（正则 `^\s*model\s*=\s*"([^"]+)"`）> 内置兜底
  `_DEFAULT_MODEL`。
- **模型目录来源（补充，已核实）**：`~/.codex/config.toml` 的
  `model_catalog_json = "cc-switch-model-catalog.json"` 指向
  `~/.codex/cc-switch-model-catalog.json`（cc-switch 模型管理工具生成的模型目录 JSON）——这是
  完整模型列表的**回退来源**；当前 adapter 已接入解析，并以 Codex 自身
  `models_cache.json` 为更高优先级动态来源。
- **代码位置**：`codex/adapter.py` `default_model` / `supported_models` /
  `_read_codex_config_toml_model` / `model_efforts`。

### 6.4 MCP 与原生 developer instructions 注入

- **现象/处理**：codex **无 `--mcp-config`**；MCP server 来自 `~/.codex/config.toml` 的
  `[mcp_servers]` 段。用 `-c 'mcp_servers.<name>...'` **内联覆盖**（实测 `codex mcp list -c '...'`
  生效）——**session 级、零文件污染、不触碰 auth.json**（API key 不泄露）。TOML 段格式对齐
  `codex mcp add` 写入形态：`command`/`args`/`[env]`；URL server 用 `url` + `transport`。同时透传
  `PAN_API_URL` 到各 server env（对齐 opencode 的 PAN_API_URL 处理）。
- **代码位置**：`codex/adapter.py` `mcp_args` / `_c_override`；app-server 进程启动时继承这些 `-c`
  覆盖，因此 MCP 在长驻 thread 中保持可用。

当 session 开启 system prompt 且 adapter 支持该能力时，worker 首次 spawn 传入
`--system-prompt`，Codex wrapper 将其转换为 `-c developer_instructions=...`。
该 prompt 位于 Codex 的 developer/instruction 层，不会作为额外 user turn 发送；无论
MCP 是否开启都适用，已有 thread resume 时不重复注入。wrapper 的 `--system-prompt`
是 Pan 内部参数，不是 Codex CLI 的公开参数。

- **代码位置**：`worker.py` `_spawn_system_prompt_args`；`codex/wrapper.py`
  `_system_prompt_opts` / `_main_loop`。

### 6.5 原生 app-server 事件、连续回合与中断

- app-server 的 `item/agentMessage/delta` / reasoning delta 被转换为 `content.part` 增量事件，前端合并
  到同一条消息；`item/completed` 转换为既有 assistant/thinking/tool 事件，`turn/completed` 转换为
  worker 所需的 `result`。
- `thread/start` 返回的 thread id 作为 Pan 的 `cli_session_id`；worker 重建时走
  `thread/resume`，已验证重启后仍能读取原生上下文。
- Codex adapter 的 `interrupt_worker` 优先向桥接进程发送控制消息，由桥接调用原生
  `turn/interrupt`；发送失败才回退到通用 kill + resume。命令/文件审批请求会先广播为
  `approval.request`，React 前端提供 Allow/Deny 控件，再由 WebSocket 或
  `POST /api/worker/{id}/control` 回传原生 JSON-RPC response；UI 断开或超过 120 秒时安全拒绝。
  其它用户输入请求仍使用保守兜底，避免无人值守 worker 无限挂起。
- **代码位置**：`codex/app_server_wrapper.py`；`worker.py` `interrupt_worker`；
  `web/src/hooks/useWebSocket.ts` 增量合并。

### 6.6 权限模式与 resume 动态切换

- **处理**：Codex adapter 暴露 `read-only` 与 `workspace-write` 两个自动批准档位，分别映射
  `sandbox_mode` 与 `approval_policy="never"`；保留 `approve` 作为 workspace-write 的兼容别名，
  `bypass` 继续映射 `--dangerously-bypass-approvals-and-sandbox`。
- `-c` 覆盖在 wrapper 的 resume 路径中保留，因此 read-only/workspace-write/approve 的设置切换
  会作用于后续 turn；`bypass` 的一次性 flag 也被显式保留，避免 resume 后 MCP 调用因审批策略
  不一致而失败。
- **代码位置**：`codex/wrapper.py` `_filter_resume_opts` / `_build_codex_args`；
  `codex/adapter.py` `permission_mode_args`。

### 6.7 thread cwd 归一化为 git 根

- **现象**：workdir 在 git 仓库内时，codex 记录的 **thread.cwd 是仓库根而非子目录**。
  `list_sessions(cwd=workdir)` 按 cwd **严格相等**过滤（`_norm_path` 大小写/分隔符归一）→
  在该 workdir 下可能**看不到**对应会话。
- **根因**：codex 在 git 仓库内运行时会向上归一化 cwd 到仓库根；已实测（`~/.codex/state_5.sqlite`
  `threads.cwd`）部分 thread 记录为 `\\?\D:\project\pan-codex-adapter`（仓库根），而带
  `--skip-git-repo-check` 且显式 `-C workdir` 的 thread 保留子目录
  `\\?\D:\project\pan-codex-adapter\data\workdirs\codex-e2e`。另注意 codex 记录的 cwd 常带
  `\\?\` 长路径前缀（`_norm_path` 已处理）。
- **处理**：`list_codex_sessions` 过滤时允许记录的 thread cwd 作为 Pan workdir 的祖先，使用带
  分隔符边界的前缀匹配（`repo` 不会匹配 `repo-other`）；仍保留大小写/分隔符归一化。
- **代码位置**：`codex/sessions.py` `_cwd_matches` / `list_codex_sessions`。

### 6.8 fork：DB 行复制与 resume

- **现象/处理**：codex CLI 无 headless `--fork`（`codex fork` 是交互 picker）。fork 直接复制 DB 行：
  - `state_5.sqlite` `threads` 建新线程（复制全列、新 id、title=name、parent 记录到
    `thread_spawn_edges`）；
  - `thread_history_1.sqlite` 复制 `thread_items` / `thread_turns` 到新 thread_id（resume 时 codex
    从 history DB 重建上下文）；
  - **`rollout_path` 指向按新 id 生成的新路径**（`sessions/<y>/<m>/<d>/rollout-...jsonl`），首次
    resume 时 codex 会新建该文件。
- **验证（2026-08-29）**：本机 Codex `state_5.sqlite` 中的 fork 子 thread 已成功产生新
  `rollout-*.jsonl`，并在复制的历史之后继续写入新 user/assistant turns；说明首次
  `codex exec resume <child_id>` 能加载复制的 history DB 并续写新 rollout。
- **代码位置**：`codex/sessions.py` `fork_codex_session`。

### 6.9 遗留：事件命名 snake_case vs camelCase

- **现象**：codex **live stdout 用 snake_case**（`agent_message` / `command_execution`），**持久化
  `thread_items` 用 camelCase**（`agentMessage` / `commandExecution`）。同一字段两种拼写。
- **处理**：统一「去掉下划线后小写」归一匹配（`type.replace("_","").lower()`），两种都兼容。
- **代码位置**：`codex/adapter.py` `extract_assistant_blocks`（`:271-276`）；
  `codex/sessions.py` `_item_to_block`；`codex/wrapper.py` `_forward_and_collect`（两拼写都判）。

### 6.10 resume 只透传 `-c` 类覆盖

- **现象/处理**：`codex exec resume <thread_id>` **不接受 `-C`**（实测报 `unexpected argument
  '-C'`），也无需 `-C`（thread 已记住 cwd）。wrapper 在 resume 时透传 `-c <value>` 对
  （mcp_servers / model_reasoning_effort / model 等覆盖），并保留审批相关 flag；其它一次性
  flag 丢弃（model 用 `-c model=`表达，不依赖 `--model`）。resume 统一加
  `--skip-git-repo-check`。
- **代码位置**：`codex/wrapper.py` `_build_codex_args` / `_filter_resume_opts`。

### 6.11 `--skip-git-repo-check`

- **现象/处理**：codex 默认要求 git 仓库，非 git 目录会报错/拒绝。wrapper 每次 `codex exec` 都加
  `--skip-git-repo-check` 兜底（workdir 可能是非 git 目录）。
- **代码位置**：`codex/wrapper.py` `_build_codex_args`。

### 6.12 enrich：rollout JSONL `token_count` 聚合增量

- **处理**：用法数据存于 rollout JSONL 的 `event_msg`（`payload.type=token_count` 的
  `last_token_usage` / `total_token_usage`）。`get_raw_usage` 读全文件取**最后一次** token_count，
  返回 session 级聚合；`enrich_after_result` 用 `adapter_config["codex_prev_usage"]` 快照做增量
  diff。同时用存储中的 model 回填 `s.model`（JSONL 事件无 model 字段）。
- **代码位置**：`codex/adapter.py` `enrich_after_result`；`codex/sessions.py`
  `get_codex_raw_usage` / `_iter_jsonl`。

### 6.13 其它

- **存储**：两个 SQLite——`~/.codex/state_5.sqlite`（`threads` 元数据 +
  `thread_spawn_edges` fork 关系）+ `~/.codex/thread_history_1.sqlite`（`thread_items` /
  `thread_turns`）；`sessions/<y>/<m>/<d>/rollout-*.jsonl` 完整事件日志。不用
  `codex session list` / `codex exec resume --last`（CLI 视角且受 cwd 过滤），直接读 DB 权威。
- **wrapper 子进程 stdin**：`stdin=DEVNULL`（见 §1.2）。

---

## 7. enrich 落账差异（stream vs oneshot）

| 执行模式 | enrich 调用点 | 效果 |
|---|---|---|
| stream（`_read_stdout` result 处理） | `adapter.enrich_after_result(s)`（worker.py:479-494） | 正常落账 |
| oneshot（`_consumer_oneshot`） | **不调用** enrich | **用量不落账** |

- **影响**：cbc 的 oneshot 模式、以及**仅 oneshot 的 claude**，本轮 token/credit 不会写入
  `session.raw_usage`。
- **特别影响 claude**：claude 的 cost 权威来源在 stdout result 事件（JSONL 无 cost），依赖 enrich
  从 `_PENDING_RESULT_USAGE` 取——oneshot 不调 enrich 则 **cost 一并丢失**（token 兜底可从 JSONL
  读回，cost=0）。
- **代码位置**：`packages/core/worker.py` `_read_stdout`（enrich 在 result 分支）vs
  `_consumer_oneshot`（无 enrich）。

---

## 8. 相关链接

- 执行模式设计：`docs/design/adapter-p1-oneshot.md`、`docs/design/adapter-architecture.md`
- kimi MCP 方案 C：`docs/design/kimi-mcp-solution.md`；opencode 适配：`docs/design/opencode-adaptation.md`
- cbc MCP defer 机制：`docs/references/cbc-mcp-defer-机制.md`、`docs/cbc-mcp-踩坑记录.md`
- adapter 代码位置：`packages/core/adapters/<cli>/adapter.py`（5 个 adapter 全部在本仓库 main）
- opencode 模型 TTL/多段名：commit 5787937（已合入 main）
