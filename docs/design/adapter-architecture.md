# Pan CLI Adapter 抽象层架构研究

> 状态：研究结论 + **P0 三项已全部落地**（P0-1 共享 MCP helper `adapters/mcp.py` / P0-2 sessions provider 化 `SessionsProvider` Protocol + registry / P0-3 本文 §8 模式差异表）；P1 执行模式显式化亦已实现（`execution_modes` + `oneshot_args`，见 adapter-p1-oneshot.md）。2026-08-27 更新：claude / codex adapter 已接入（共 5 个），§8 表已补齐两列。
> 日期：2026-08-26（08-27 更新）
> 研究问题：adapter 抽象层是否应当增加/改变架构，以适应越来越多的 agent CLI（cbc / kimi / opencode / claude code / gemini cli / codex / aider）。
> 范围：packages/core/adapters/base.py 协议、worker.py 消费逻辑、server.py 分发点、外部 CLI 调研。

---

## 1. 现状盘点：CliAdapter 协议 vs 隐含假设

CliAdapter 协议（`packages/core/adapters/base.py`）约 22 个方法，可分为 5 组：

| 组 | 方法 | 隐含假设 |
|---|---|---|
| 元信息 | name/default_model/supported_models/effort_values/permission_modes/default_permission_mode/supports_resume/supports_fork/supported_settings | 无传输假设，通用 |
| 进程启动 | base_args/model_args/thinking_args/effort_args/permission_mode_args/resume_args/fork_args/build_spawn_args | **stream 长驻**：build_spawn_args 返回"一个常驻进程"的 argv（worker 起一次，跨消息复用） |
| stdin 编码 | encode_user_message | **stream 长驻**：写一条消息到长驻进程的 stdin；隐含"stdin 是 JSON 流" |
| stdout 解析 | parse_event/event_type/is_init_event/extract_session_id/extract_model/is_assistant_event/extract_assistant_blocks/is_result_event/is_result_error/extract_result_text | 通用（事件模型组），**假设 stdout 是逐行 JSON 事件流** |
| takeover/enrich | takeover_command/enrich_after_result | **一次性/存储**：enrich 假设 CLI 把 transcript 落盘（JSONL/wire.jsonl/SQLite），事后读取 |

关键结论：**协议里真正隐含 "stream-json 长驻" 假设的只有两个面——① build_spawn_args 的"一次 spawn 复用"语义，② encode_user_message 的"写 stdin 流"语义。stdout 事件解析组本身是通用的（事件模型）**。

### cbc / kimi 各自如何满足/绕过

**cbc（原生长驻）**：`base_args()` = `-p --output-format stream-json --input-format stream-json -y`（cbc/adapter.py:169），encode_user_message 写 `{"type":"user","message":{...}}`。同时存在**第二条路径** `base_args_stream()`（:174，去掉 input-format）+ `_consumer_mcp`（worker.py:1188）——当 session 配了 MCP 且 `output_mode=="oneshot"` 时，每条任务 spawn 一个一次性 `-p` 进程，prompt 作末参。**cbc 同时是"长驻"与"一次性"两种形态，且 worker 里的 `_use_oneshot_mcp`（worker.py:305）与 `_consumer_mcp` 的拼装逻辑是 cbc 特定的**（prompt 作末参、--system-prompt、--mcp-config、result/init 事件解析、`_extract_cbc_error`）。

**kimi（wrapper 包装一次性）**：CLI 无 stdin 长驻，adapter 的 `base_args()`（kimi/adapter.py:137）返回 `python wrapper.py --kimi-path ...`——**把"常驻"假设计算到 wrapper 进程上**。wrapper（wrapper.py:102 `_main_loop`）内部逐条 `kimi -p <text> --output-format stream-json [-S sid]`，转发 stdout 事件、合成 result。encode_user_message 变成 wrapper 的 stdin 协议（`{"text": text}`，与 cbc 形状不同但同样是逐行 JSON）。

→ **协议按"原生长驻"设计的两个面，都被 kimi 用"wrapper 中间层"绕过了**。这本身证明协议足够灵活，但代价是：wrapper 是第三个"传输实现"，且 wrapper 的健壮性、超时、session_id 提取都要自己维护。

---

## 2. 执行模式：当前是隐式建模，且 cbc 特定逻辑泄漏在 worker 里

worker.py 里**三条执行路径 + 一个 cbc 专用判定矩阵**，都不在协议里：

- `_use_oneshot_mcp(s)`（worker.py:305-315）：`MCP配置 && output_mode=="oneshot"` → one-shot，否则 stream。判定项 `output_mode` 是 cbc 的 MCP 专用字段。
- `_consumer`（:642）三选一：stream / stream+MCP / one-shot MCP。
- `_consumer_mcp`（:1188）整体是 cbc 形状：`base_args_stream`（hasattr 探测）→ model_args/permission_mode_args/effort_args → resume_args → mcp_args（hasattr 探测）→ `--system-prompt`（`s.system_prompt and not s.cli_session_id`）→ prompt 作末参。
- `_spawn_process`（:1760）：`cwd=s.workdir`、stdin=PIPE——**假设长驻进程有 stdin**（one-shot 时也建了 stdin pipe 但不用）。

这些用 `hasattr(adapter, 'base_args_stream')` / `hasattr(adapter, 'mcp_args')` 探测协议外方法（worker.py:1207/1210/1223），说明**协议边界已经破了**——`mcp_args`、`base_args_stream` 事实上成了非正式协议成员。

**结论：执行模式值得显式建模。** 但不需要全新的传输层；最小方案是给协议加一个能力字段 + 把 one-shot 拼装搬进 adapter：

- `execution_modes: list[str]`（`["stream"]` / `["oneshot"]` / `["stream","oneshot"]` / 未来 `["acp"]` / `["attach"]`）。cbc=`["stream","oneshot"]`，kimi=`["stream"]`（wrapper 伪装）、opencode/gemini/codex=`["oneshot"]`。
- 新增 `oneshot_args(s: Session, text: str) -> list[str]`，把 `_consumer_mcp` 里 cbc 特定的拼装（prompt 末参、system-prompt、mcp 注入、resume）交给 adapter。worker 的 one-shot consumer 变成通用循环：`oneshot_args` → spawn（无 stdin）→ 收集 stdout → 走既有的 `parse_event` 事件模型。

这个改动的收益：opencode/gemini/codex 接入时不再需要 `_consumer_mcp` 再加适配分支；kimi 继续用 wrapper 不动。成本：worker 重构一个 consumer + 协议加 2 个成员（低风险，改动集中在 worker.py 的 `_consumer_mcp` 与 cbc adapter）。

**更彻底但暂不做的选项**：把"单轮执行"整个交给 adapter（`async def run_turn(s, text) -> TurnResult`），worker 退化为薄壳。这能优雅支持 ACP（JSON-RPC stdio 长驻）与 opencode serve（HTTP attach）——这两种传输**写不进现在的 stdin/stdout 循环**。但成本高（现有 stream 的防抖落盘、watchdog、task_done 信号、MCP 叠加逻辑都依赖 worker 拥有循环），**只有真正引入 ACP/serve 时才立项做**。

---

## 3. MCP 注入：三套机制，但共享同一个"描述符构造"可抽离

| adapter | 注入方式 | 文件位置 | 信任边界问题 |
|---|---|---|---|
| cbc | `--mcp-config data/mcp-configs/<sid>.mcp.json`（cbc/adapter.py:235） | Pan 自有 data 目录，不污染 workdir | 无 |
| kimi | `mcp_args` 返回 `[]`，但**副作用写** `<workdir>/.kimi-code/mcp.json`（kimi/adapter.py:193-255） | 项目级，需要 workdir 被信任，否则跳过 | 有（非交互无法应答信任提示） |
| opencode | opencode.json 的 `mcp` 段 | 项目/用户级配置文件，`opencode mcp add` 管理 | 待验证 |
| claude code | 项目 `.mcp.json` 自动发现 + `claude mcp add`（也有 `--mcp-config`） | 项目级 | 有 |
| gemini | `gemini mcp add` 写入 settings 文件 | 用户级 | 无 flag 指向外部文件 |
| codex | config.toml `[mcp_servers]` | 用户级 | 无 flag |

差异点是"配置落点 + 是否有 flag 指向外部文件"；**共同点是 MCP server 描述符结构完全一致**（name/command/args/env/type/transport/headers + 注入 `PAN_AGENT_SESSION_ID/TITLE`）。cbc/adapter.py:258-280 与 kimi/adapter.py:221-247 是**几乎逐行的重复代码**。

**结论：不需要协议层抽象 MCP（`mcp_args` 已足够表达"返回注入 flag"），但应抽一个共享 helper**（如 `adapters/mcp.py: build_mcp_servers(servers, session) -> dict`）把描述符构造 + pan/pan-qq 身份注入收敛，cbc/kimi 复用。同时修正一个味道：kimi 把"写文件副作用"藏在 `mcp_args`（一个 args 构造器里）——建议改为 `prepare_mcp(s)`（写文件）+ `mcp_args(s)`（仅返回 flag），语义分离。

---

## 4. 会话存储 / 历史 / enrich：结构同构、命名不同，server 按 adapter 硬分派

cbc sessions（sessions.py）与 kimi sessions（sessions.py）**实现了几乎同一组能力**：

| 能力 | cbc | kimi |
|---|---|---|
| 列项目/工作区 | list_cbc_projects/list_cbc_sessions/browse_cbc_tree | list_kimi_workspaces/list_kimi_sessions |
| 解析历史 | parse_cbc_history | parse_kimi_history |
| usage | get_raw_usage | get_raw_usage |
| 标题 | get_session_title/write_custom_title | get_session_title/write_custom_title |
| fork | fork_cbc_session | fork_kimi_session |

但**命名不统一、签名不统一**（cbc 的 `project_cwd/project_dir` vs kimi 的 `workdir`），导致 server.py 全按 adapter 硬分派：

- `/api/cbc/*`（server.py:1663-1729）与 `/api/kimi/*`（:1869-1883）——import 逻辑 90% 重复（parse history + get_raw_usage + dedup cli_session_id + create/reimport），只是调用的函数名不同（:1728 vs :1883）。
- `api_branch_session`（:1052）：`if s.adapter == "kimi": ... else: cbc`（:1076-1084），历史/usage 解析同样二分支（:1090-1096）。
- `api_rename_session`（:1012）：`if s.adapter == "kimi"` 才写回原生存储（:1037）。

**每新增一个 adapter = 新增一套 `/api/<name>/*` 端点 + branch/rename 两个 if 分支**，这是目前最明显的不可扩展点。

**结论：值得统一成 sessions provider 接口。** 定义 `SessionsProvider` Protocol（list_projects/list_sessions/parse_history/get_raw_usage/get_session_title/write_custom_title/fork），让 cbc/kimi 的 sessions 模块对齐签名；新增**通用 import/branch 端点**（如 `/api/adapters/{adapter}/sessions[/import]`）按 adapter 名取 provider。旧 `/api/cbc/*`、`/api/kimi/*` 保留为薄包装。这样 import/branch/rename 的 server 逻辑只剩一份。注意保留 cbc 独有的 browse 树（provider 可带可选能力位）。这是中风险、纯重构，收益是每个新 adapter 省 ~150-200 行 server 代码。

---

## 5. 前端 settings：机制够通用，不需要动

`/api/adapter/config`（server.py:1206）返回 models/effortValues/permissionModes/supportedSettings；React 侧 InputRow.tsx / SettingsPopover.tsx 用 `supportedSettings.includes(name)` 显隐 model/permissionMode/thinking/effort。kimi 用 `supported_settings=["model"]` 已验证"能力声明驱动 UI"的模式成立。

两个小缺口（非阻塞）：
- `output_mode`（_session_to_api:272）暴露给前端但无 UI，是 cbc-MCP 专用字段——若 execution_mode 显式建模，前端将来可据 `execution_modes` 展示模式选择。
- 未来可能需要新设置键（如 opencode `--agent`、claude 的模型变体），`supportedSettings` 字符串枚举机制可容纳，无需改协议。

---

## 6. 未来 adapter 展望（2026-08 调研）

| CLI | headless 入口 | stdin 流式长驻 | resume | fork | MCP 配置 | 存储 |
|---|---|---|---|---|---|---|
| **claude code** | `claude -p --output-format stream-json --input-format stream-json` | **有**（与 cbc 同协议——cbc 是其分支） | `--resume <id>` | `--fork-session` | 项目 `.mcp.json` 自动发现 + `--mcp-config` | `~/.claude/projects/<sanitized>/<sid>.jsonl` |
| **gemini cli** | `gemini -p --output-format stream-json` | 无（`cat file \| gemini` 只喂内容） | `-r/--resume <id\|latest>` + `--list-sessions` | 无 | `gemini mcp add`（settings 文件） | `~/.gemini/` 会话 |
| **codex** | `codex exec "prompt"` | 无 | `codex exec resume --last` / `--session` | 有 `/fork` | config.toml `[mcp_servers]` | `~/.codex/sessions/*.jsonl` |
| **opencode** | `opencode run [msg] --format json` | 无（prompt 作位置参；另有 `opencode serve` HTTP attach） | `--continue` / `--session <id>` | `--fork` | opencode.json `mcp` 段 + `opencode mcp add` | **SQLite**（`opencode export/import`、`opencode db`） |
| **aider** | `aider -m "msg"` | 无 | 无会话 resume（只有 /save /load 文件装配） | 无 | 无（文档未覆盖） | `.aider.chat.history.md`（git 原生） |
| **kimi**（已有） | `kimi -p --output-format stream-json` | 无（wrapper 包装） | `-S <id>`（不重放历史） | 目录复制 | 项目 `.kimi-code/mcp.json` 自动加载 | `<session>/agents/main/wire.jsonl` + state.json |

**共性提炼**：
1. **事件模型趋同**：几乎全部收敛为"stdout 逐行 JSONL"，事件类型 init/message/assistant/tool_use/result——这正是协议第 3 组（parse_event 系列）已覆盖的。**stdout 解析组无需改动**。
2. **headless 形态趋同**：全有 `-p` 式一次性入口。**持久 stdin 流式只有 cbc/claude 两家**；其余要么 per-message spawn（oneshot），要么 wrapper，要么（未来的）ACP/serve。
3. **resume 概念普遍、机制各异**：flag（cbc/claude/gemini/kimi）vs 子命令（codex `resume`）vs `--continue/--session`（opencode）。协议 `resume_args(s)` 的"返回 argv 片段"模型对 flag 型适配良好；codex 的"子命令"型需要 adapter 在 base_args 里就带上 `exec`（可表达，但 `supports_resume` 语义要逐 adapter 注释）。
4. **MCP 全支持、落点全不同**（见 §3）。
5. **ACP 成为去事实标准**：kimi（`kimi acp`）、gemini（`--experimental-acp`）都已提供 JSON-RPC over stdio 长驻——若未来主接 ACP，才真正需要 transport 层抽象。

**覆盖性评估**：现有单层协议 + worker 三路径能覆盖除 ACP/serve 外的全部场景——
- claude code = CbcAdapter 的近亲（同 flag、同 JSONL 布局，只是路径 `~/.claude`），**协议设计被它反向验证**；
- gemini/codex/opencode = kimi 式 wrapper 或 oneshot（需 §2 的 `oneshot_args` 支持最干净）；
- aider = 最难，无 resume、无结构化事件流、文本输出，接入收益最低，建议降级为"文本一次性 + 无 enrich"。

---

## 7. 结论与建议（按优先级）

**总体判断：当前协议的单层事件模型是"对的"，不需要推翻；但三个地方需要补**——①执行模式显式化（去掉 hasattr 探测与 cbc 特定 consumer），②MCP 描述符共享，③sessions provider 统一（消 server 硬分派）。**不推荐现在引入 transport/protocol 两层抽象**：worker 拥有循环带来了防抖落盘/watchdog/task_done 等大量既有语义，拆层成本高、现阶段收益低；只有当 ACP 或 opencode serve 成为主接路径时才值得立项。

### P0 低风险、立即可做（纯重构，不改协议语义）
1. **抽共享 MCP helper**：`adapters/mcp.py` 收敛 cbc/adapter.py:258-280 与 kimi/adapter.py:221-247 的重复描述符构造 + pan/pan-qq 身份注入。零行为变化。
2. **sessions provider 化 + 通用 import/branch**：定义 `SessionsProvider` Protocol（list_projects/list_sessions/parse_history/get_raw_usage/get_session_title/write_custom_title/fork），cbc/kimi sessions 模块对齐签名；新增通用 `/api/adapters/{adapter}/sessions[/import]`，`api_branch_session`/`api_rename_session` 改走 provider。旧端点留薄包装。消除每 adapter ~200 行 server 重复。
3. **文档沉淀**：本文件即起点——每个 adapter 的模式差异（transport/MCP/storage/resume）记录成表，作为协议外的"约定层"。

### P1 中风险、下个 adapter（opencode/gemini）落地时做
4. ~~**执行模式显式化**~~：**已完成（2026-08，commit `275f7e2`）**——协议加 `execution_modes` + `oneshot_args`，worker 去 `hasattr` 探测，见 `docs/design/adapter-p1-oneshot.md`。
5. ~~**MCP 语义分离**~~：**已由 kimi 方案 C（KIMI_CODE_HOME 隔离）取代/覆盖**——kimi `mcp_args` 返回 `--kimi-home`，文件副作用移入 `_prepare_kimi_home`（见 `docs/design/kimi-mcp-solution.md`）。

### P2 大改动、需立项（仅在确定要接 ACP/serve 时）
6. **transport 层**（adapter 拥有单轮执行 `run_turn`）：支持 `kimi acp` / `gemini --experimental-acp` 等 JSON-RPC stdio 长驻与 opencode serve HTTP attach。届时 worker 退化为调度层，防抖落盘/watchdog 语义需要重新设计归属。
7. 若同时接 ACP + 多 CLI，才考虑正式的两层抽象（transport=进程通信，protocol=事件模型）。

### 反向验证与风险提示
- ~~**claude code 应作为下一个 adapter**~~：**已实现（2026-08，commit `b46eb11`）**，其后 codex（`c8f6f96`）亦接入，预言兑现。
- aider 接入收益最低（无 resume/结构化输出），建议功能位（feat 列表）挂低优先级。
- 同事在改 kimi adapter/server.py（本 worktree 未提交），上述重构建议应在 kimi 适配收敛后再落地，避免冲突。（历史语境：kimi 适配已于 2026-08-25 收敛合入。）

---

## 8. Adapter 模式差异表（P0-3，约定层）

协议外的「约定层」：每个 adapter 的执行模式 / 事件协议 / 存储 / resume/fork /
MCP / enrich 的既有差异，接入新 CLI 前先对照本表。表格基于已落地 adapter 的
代码现状（2026-08-26 建表时为 cbc/kimi/opencode 三列，2026-08-27 补齐 claude/codex 两列，共 5 个）。

| 维度 | cbc | kimi | opencode | claude | codex |
|---|---|---|---|---|---|
| **执行模式** | stream 长驻（原生 stdin/stdout）+ one-shot MCP（`base_args_stream`，worker `_consumer_mcp`） | wrapper 长驻（wrapper.py 内逐条 `kimi -p`） | wrapper 长驻（wrapper.py 内逐条 `opencode run`） | one-shot（`execution_modes=["oneshot"]`，`claude -p` stream-json） | stream 长驻（wrapper.py + `codex` proto/stream 长驻，`execution_modes=["stream"]`） |
| **事件协议** | stdin JSONL `{"type":"user","message":...}`；stdout stream-json（system.init/assistant/result/error） | wrapper stdin JSONL `{"text":text}`；`kimi -p --output-format stream-json`（meta/assistant/result + content.part，wrapper 转发/合成 result） | wrapper stdin JSONL `{"text":text}`；`opencode run --format json`（step_start/text/tool_use/step_finish/error，wrapper 合成 result） | `-p --output-format stream-json`（init/assistant/result，事件形状与 cbc 同源复用解析） | wrapper stdin JSONL；`codex` 事件流（task_started/agent_message/token_count 等，wrapper 转发/合成 result） |
| **session 存储** | JSONL：`~/.codebuddy/projects/<sanitized-cwd>/<sid>.jsonl`（+ `.meta.json`） | wire.jsonl：`~/.kimi-code/sessions/<ws>/session_*/agents/main/wire.jsonl` + `state.json`（`session_index.jsonl` 索引） | SQLite：`~/.local/share/opencode/opencode.db`（session/message/part 表，事件溯源） | JSONL：`~/.claude/projects/<encoded-cwd>/<sid>.jsonl` | `~/.codex/`：`state_5.sqlite`（threads 元数据，含 rollout_path）+ `sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl`（完整事件日志） |
| **resume / fork** | `--resume <id>` / `--fork-session`（JSONL 复制 + meta.json） | `-S <id>`（恢复上下文、不重放历史）/ 目录复制 + 注册新 session（无 native fork） | `--session <id>`（wrapper 持有并复用 sessionID）/ SQLite 行复制（parent_id 指向父） | `--resume <id>` / fork 经 JSONL 复制（`sessions.fork_session`） | resume 保留 session（wrapper thread 复用 + `-c` 覆盖 resume 通用）/ fork 物化 rollout 复制并注册新 thread |
| **MCP 落点** | Pan 自有 `data/mcp-configs/<sid>.mcp.json` + `--mcp-config` flag（不污染 workdir） | `KIMI_CODE_HOME` 隔离 home（方案 C）：`_prepare_kimi_home` 写 `<隔离home>/mcp.json`，`mcp_args` 返回 `--kimi-home` | 项目级 `<workdir>/opencode.json`（gitignored 运行时产物）注入 `mcp` 段 | Pan 自有 `data/mcp-configs/<sid>.mcp.json` + `--mcp-config` flag（与 cbc 同格式，共享 helper） | `-c 'mcp_servers.<name>...'` 内联覆盖（session 级、零文件污染、不触碰 auth.json） |
| **enrich 数据源** | JSONL assistant 事件 `providerData.rawUsage`（per-model request_count 游标） | wire.jsonl `usage.record`（model+usage，`time` epoch ms 游标） | SQLite `session.tokens_*/cost` 聚合（会话级快照差值游标） | JSONL result 事件 usage（`_read_claude_jsonl_usage`） | rollout JSONL `event_msg`（`payload.type=token_count`） |
| **MCP 注入 helper** | 共享 `adapters/mcp.py: write_mcp_json` + `--mcp-config` 返回 | 共享 helper（经 `_prepare_kimi_home` 写入隔离 home） | 写项目级 opencode.json | 共享 `adapters/mcp.py: write_mcp_json` + `--mcp-config` 返回 | 无文件 helper（`-c` 内联构建） |
| **sessions provider** | `cbc/sessions.py`（SessionsProvider 协议，含 `session_exists`/`project_dir_to_path` 可选能力） | `kimi/sessions.py`（协议函数，无 `session_exists` → import 跳过 guard） | `opencode/sessions.py`（协议函数，含 `session_exists`） | `claude/sessions.py`（含 session_exists，rglob 兜底跨目录查找） | `codex/sessions.py`（SQLite threads + rollout 文件双源） |
| **导入端点** | `/api/cbc/*`（薄包装）+ 通用 `/api/adapters/{adapter}/sessions[/import]` | `/api/kimi/*`（薄包装）+ 通用同上 | 通用 `/api/adapters/{adapter}/sessions[/import]` + `/api/opencode/sessions[/import]` | 通用同上 | 通用同上 |
