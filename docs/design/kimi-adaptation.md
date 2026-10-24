# Kimi Code 适配设计文档

> 目标：以 Pan 在 cbc 上实现的功能全集为基准，补齐 kimi code 适配，使同一套 Pan 功能（spawn/resume/fork/takeover/enrich/import/MCP）由 kimi code 驱动。
> 状态：设计定稿，实现派发中。测试模型：`moonshot-cn/kimi-k2.6`（已在 ~/.kimi-code/config.toml 配置）。
> 日期：2026-08-25

## 1. cbc 功能基准（Pan 在 cbc 上已实现的全部能力）

### Adapter 层（packages/core/adapters/cbc/adapter.py）
| 能力 | cbc 实现 | 协议方法 |
|---|---|---|
| stream 长驻 | `-p --output-format stream-json --input-format stream-json -y` | base_args + build_spawn_args |
| one-shot MCP | `-p --output-format stream-json -y`（无 input-format） | base_args_stream |
| 模型列表 | config.json > `cbc --help` 解析 > 硬编码 | supported_models |
| resume | `--resume <id>` | resume_args |
| fork | `--fork-session` | fork_args |
| thinking | `--settings {"alwaysThinkingEnabled":...}` | thinking_args |
| effort | `--effort <v>`（需 thinking 开启） | effort_args |
| 权限模式 | `--permission-mode <mode>` | permission_mode_args |
| MCP | 写 `data/mcp-configs/<sid>.mcp.json` + `--mcp-config`，注入 PAN_AGENT_SESSION_ID/TITLE | mcp_args |
| stdin 编码 | stream-json 用户消息 | encode_user_message |
| stdout 解析 | system.init / assistant / result / error | parse_event + 系列提取方法 |
| takeover | `--resume <id> --system-prompt`（node 解析 .cmd shim） | takeover_command |
| enrich | 读 JSONL 增量 rawUsage（per-model request_count 游标） | enrich_after_result |
| 可执行路径 | config > PAN_CBC_PATH > PATH > 回退；.cmd → node entry | _resolve_cbc_path/_argv |

### Sessions 层（packages/core/adapters/cbc/sessions.py）
list_cbc_projects / list_cbc_sessions / browse_cbc_tree / parse_cbc_history / get_raw_usage / get_session_title / write_custom_title / fork_cbc_session

### 服务端（packages/web/server.py）
/api/cbc/projects、/api/cbc/sessions、/api/cbc/browse、/api/cbc/sessions/import；/api/adapter/config、/api/models、/api/adapters 按 adapter 名分发；branch 按 adapter 分派

### Worker 集成（packages/core/worker.py）
- stream 模式：`build_spawn_args` 长驻进程，stdin 写消息 / stdout 读事件
- one-shot MCP 模式：`base_args_stream` + resume + mcp_args + `--system-prompt` + prompt 作末参，每次任务新进程
- system_prompt 注入：纯 stream 首条消息；MCP `--system-prompt`；仅 `not s.cli_session_id` 时注入
- enrich：result event 后调 `enrich_after_result`，累加 raw_usage/total_usage

## 2. kimi 现状与差距

### 已实现（packages/core/adapters/kimi/）
- **adapter.py**（278 行）：name、models（config.json > config.toml `[models."..."]` > 硬编码）、resume/fork、build_spawn_args（走 wrapper）、encode/parse、takeover、事件解析
- **wrapper.py**（184 行）：长驻子进程，内部循环 `kimi -p <text> --output-format stream-json [-m model] [-S sid]`，转发 stdout，合成 result 事件
- **sessions.py**（429 行）：list_kimi_sessions / list_kimi_workspaces / parse_kimi_history / get_raw_usage / get_session_title / fork_kimi_session
- **server.py**：/api/kimi/workspaces、/api/kimi/sessions、/api/kimi/sessions/import、branch 分派
- **config.py**：`kimi` 配置段（model/permission_mode/always_thinking_enabled/effort）

### 差距清单（对照第 1 节）
| # | 差距 | 影响 | 行动 |
|---|---|---|---|
| G1 | `extract_model` 返回 None | 会话 model 不自动回填 | 从 wrapper 转发事件提取（见 §4.4） |
| G2 | `enrich_after_result` 返回 None | 无 token/credit 消耗统计 | 接 kimi_sessions.get_raw_usage 增量（见 §4.3） |
| G3 | 无 `base_args_stream` | one-shot MCP 模式 fallback 到 wrapper args 并 append prompt → bug | 实现或显式声明不支持 |
| G4 | `mcp_args` 返回 [] | kimi 会话无法用 Pan MCP（pan/pan-qq server） | 按 kimi mcp.json 机制设计（见 §4.5） |
| G5 | `takeover_command` 硬编码 `kimi` | Windows 下 PATH 无 kimi → 失效 | 用 `_KIMI_PATH` |
| G6 | `supported_settings=["model"]`，thinking/effort/permission 标注不可用 | 前端 settings 面板能力受限 | 按 kimi 真实能力细化（见 §4.6） |
| G7 | 无 `write_custom_title` | 重命名 kimi 会话不落 kimi 存储 | 补 kimi_sessions.write_custom_title |
| G8 | wrapper 健壮性不足 | 无超时/重试、session_id 更新面窄、异常路径 | 增强（见 §4.7） |
| G9 | `supports_resume=False` 与 `resume_args` 并存，语义不清 | 前端/worker 判断混乱 | 澄清并统一（见 §4.8） |

## 3. 侦察关键事实（2026-08-25 本地验证）

### kimi CLI 能力（`~/.kimi-code/bin/kimi.exe --help`）
- `-p/--prompt <text>`：一次性 prompt，`--output-format text|stream-json`
- `-S/--session [id]`：resume 会话；`-c/--continue`：继续上一会话
- `-m/--model <alias>`：模型别名
- `-y/--yolo`、`--auto`、`--plan`：权限模式
- `--agent` / `--agent-file`、`--skills-dir`、`--add-dir`
- **无 `--system-prompt`、无 `--input-format`、无 `--mcp-config` 参数**
- `kimi acp`：**ACP (Agent Client Protocol) server over stdio**（JSON-RPC 2.0，支持会话恢复/工具调用通知）——后续升级候选
- 子命令：export / provider / acp / web / login / doctor / migrate

### config.toml（~/.kimi-code/config.toml）
- `default_model = "moonshot-cn/kimi-k2.6"`
- `[thinking] enabled=false effort="high"`（thinking 是全局配置，CLI 无独立 --thinking 参数）
- `[providers.<name>]`（kimi 类型 provider + api_key + base_url）
- `[models."<alias>"]`（provider/model/max_context_size/capabilities）→ supported_models 来源；当前含 `moonshot-cn/kimi-k2.7-code`、`moonshot-cn/kimi-k2.6`

### MCP 机制
- kimi 通过 **mcp.json** 配置 MCP server（用户级 `~/.kimi-code/mcp.json`、项目级 `<workdir>/.kimi-code/mcp.json`？，同名条目项目级覆盖用户级），TUI 内 `/mcp-config` 交互管理。当前用户级 mcp.json 不存在。
- **folder-trust 门禁**：project 级 MCP 需文件夹信任应答；`-p` 非交互模式无法应答 → 静默跳过。用户级 MCP 不受此门禁约束。
- **`KIMI_CODE_HOME` 环境变量**可重定向 kimi 整个用户目录（含 config.toml + mcp.json），重定向后的目录等价「用户级」，可绕过 folder-trust（见 §4.5 方案 C）。
- 官方文档：https://www.kimi.com/code/docs/kimi-code-cli/customization/mcp.html
- 全量调研见 `docs/design/kimi-mcp-solution.md`。

### wire.jsonl 事件模型（`<session>/agents/main/wire.jsonl`）
- `metadata`（protocol_version）
- `config.update`（systemPrompt、**modelAlias**、thinkingEffort）
- `tools.set_active_tools`（内置工具：Read/Write/Edit/Grep/Glob/Bash/TaskList/Skill/WebSearch/Agent/FetchURL…）
- `turn.prompt`（用户输入）
- `context.append_message`（user/assistant 消息）
- `context.append_loop_event`：`step.begin` / `content.part`（type: think|text）/ `tool.call`（name/args）/ `tool.result` / `step.end`（**usage**）
- `usage.record`（**model + usage**：inputOther/inputCacheRead/inputCacheCreation/output，**time** epoch ms）
- `permission.set_mode`、`plan_mode.enter/cancel`
- kimi/sessions.py 的 parse_kimi_history / get_raw_usage 已解析上述事件

### stream-json stdout 输出（wrapper 已转发处理）
- `role: meta`（type=session.resume_hint，含 session_id）
- `role: assistant/thinking`（content 字符串，或结构化 blocks）
- `role: result`（is_error / result）
- `content.part` 类型事件（wire.jsonl 内层结构）
- **注意**：stdout 事件中未确认 model 字段，需实测确认（见 §4.4）

## 4. 设计决策

### 4.1 接入路径：保持 wrapper（`-p` 一次性进程）为主路径，本轮补齐
kimi 无 cbc 式的 `--input-format stream-json` 长驻模式，wrapper 已在生产验证。**本轮不动架构**，只补齐能力缺口。ACP（`kimi acp`）作为后续升级项独立调研（§6）。

### 4.2 system_prompt 注入
沿用现状：kimi 走 stream 长驻（wrapper），system_prompt 由 `_create_worker` 以首条消息注入（worker.py 已按 `not s.cli_session_id` 守卫，kimi 同样生效）。无需改。

### 4.3 enrich_after_result（G2）
- 数据源：`kimi_sessions.get_raw_usage(session_id, workdir)`（解析 usage.record → {model, rawUsage, timestamp}）
- 增量游标：cbc 用 per-model request_count 游标。kimi 的 usage.record 自带递增 `time`（epoch ms），**用 time 游标**更简单可靠：Session 记录 `kimi_last_usage_ts`（adapter_config），只返回 time > 游标的条目，然后推进游标。
- 实现位置：adapter.py 新增私有方法，参照 cbc 的 `_read_jsonl_new_entries` 结构（含 `time.sleep(0.2)` 等 kimi 写延迟补偿）。

### 4.4 extract_model（G1）
- 先实测 kimi stream-json stdout：`kimi -p "1+1" -m moonshot-cn/kimi-k2.6 --output-format stream-json` 看事件字段。
- 若 stdout 事件带 model/modelAlias 字段 → 直接从事件提取。
- 若没有 → `extract_model` 保持返回 None，model 由两处兜底：① session 创建时用户显式指定；② enrich 时从 usage.record 的 model 字段回填（此时已知真实消费模型）。

### 4.5 MCP（G4）— 方案 C：KIMI_CODE_HOME 隔离 + data 统一管理（2026-08-26 实现，hy3）
- kimi 无 `--mcp-config`，MCP 靠 mcp.json 文件。Pan 的 `data/mcp-configs/<sid>.mcp.json` 机制对 kimi 不适用（kimi 不接受该参数）。
- **根因（已验证）**：kimi `-p` 模式下 project MCP（`<workdir>/.kimi-code/mcp.json`）受 **文件夹信任（folder-trust）门禁** 约束——非交互模式无法应答信任提示，project 级 MCP 被静默跳过（`tool blocks = 0`）。用户级 `~/.kimi-code/mcp.json` 不受此门禁约束（见 `kimi-mcp-solution.md` 全量调研）。
- **方案 C 实现**：用 `KIMI_CODE_HOME` 环境变量把 kimi 的整个用户目录重定向到 Pan 托管的隔离目录。该目录下放入 `config.toml`（从真实 `~/.kimi-code/config.toml` 拷贝）+ `mcp.json`（含 pan/pan-qq server 的 stdio 启动命令 + `PAN_AGENT_SESSION_ID/TITLE` 注入）。重定向后该目录等价于「用户级」，从而**绕过 folder-trust 门禁**，pan MCP 在 `-p` 模式可用。
- **配置统一到 data/（模仿 cbc 的 `data/mcp-configs` 理念）**：
  - 隔离 HOME 根：`DATA_DIR / "kimi-homes"`，每会话一个子目录 `data/kimi-homes/<session_id>/`（含 `config.toml` + `mcp.json`）。
  - 生成逻辑：`KimiAdapter._prepare_kimi_home(s)`（`adapter.py`）——`build_mcp_servers(s)` 为空则返回 `None`（无 MCP 会话不加 `--kimi-home`）；否则建目录、拷贝 config.toml、写 mcp.json、把路径回填 `s.adapter_config["kimi_home_dir"]`。
  - `mcp_args(s)` 返回 `["--kimi-home", <home>]`（wrapper 新增 `--kimi-home` 参数）。
  - `wrapper.py`：`_main_loop(kimi_home=...)` 在 `subprocess.Popen` 的 `env` 中注入 `KIMI_CODE_HOME=kimi_home`，整条 kimi 子进程链都读该隔离 HOME。
  - **注意**：`KIMI_CODE_HOME` 重定向的是 kimi 整个用户目录，因此 `sessions.py` 的 enrich/历史/ fork/ rename 读取（session_index、usage、title）也必须读该隔离 HOME，否则 enrich/history 会断。已通过 `kimi_home` 参数贯穿所有 sessions 函数实现。
- **清理**：`server.py` 在 `api_delete_session` / `api_batch_delete_sessions` 中调 `_cleanup_kimi_home(sid)`，删除 `data/kimi-homes/<sid>`。
- **project 级 `write_kimi_mcp_json` 保留**：交互式/已信任 workspace 场景仍可用（文件写入正确），与方案 C 不冲突；方案 C 是 `-p` 非交互主路径的解法。
- **验证**：worker 级集成测试 `scripts/kimi-mcp-probe/06_integration_wrapper.py` PASS——kimi 在 `KIMI_CODE_HOME` 隔离下成功加载 mcp.json 并调用 pan 工具（`tool_seen_in_stream=True, marker_created=True`）。

### 4.6 supported_settings / 权限 / thinking（G6）
- kimi 无 `--thinking` / `--effort` 参数（在 config.toml `[thinking]` 配置）。`thinking_args`/`effort_args` 返回 [] 合理。
- `-y/--auto/--plan` 与 `-p` 可同时使用（help 未禁止，且 `permission.set_mode` 事件存在）。但 **-p 模式下权限交互式问题无法应答**，实测确认 `-p + -y` 是否生效。
- 若 `-p -y` 生效：permission_modes 保留 `yolo/auto/plan`（标注可用），`permission_mode_args` 实现为对应 flag；`supported_settings` 扩为 `["model", "permissionMode"]`。
- 若实测不生效：维持现状（`["model"]`），并在前端对 kimi 隐藏 permission 选择。

### 4.7 wrapper 健壮性（G8）
- session_id 更新：现仅在 `session.resume_hint` 时更新；补充从其它 meta/result 事件兜底提取。
- 超时/重试：`kimi -p` 偶发 0xC0000409 崩溃（worker.py 注释已提及），wrapper 对非零退出但无文本输出的情形已有错误 result；增加 stderr 透传至 Pan 日志。
- cwd：`PAN_KIMI_CWD`/`CLICONDUCTOR_KIMI_CWD` env 已支持，需确认 session.workdir 正确传入（wrapper 的 cwd 决定 kimi workspace 归属）。

### 4.8 supports_resume 语义（G9）
- kimi `-S <id>` 恢复上下文但**不重放历史事件**（adapter 注释）。语义等价 cbc `--resume`（JSONL 续写）。
- **决策**：`supports_resume = True`（worker 据此在 one-shot MCP 路径 resume；stream 路径 wrapper 自己 -S）。同时保留注释说明与 cbc 的差异（kimi 无 init 事件回放）。

### 4.9 takeover_command（G5）
- 返回 `[self._KIMI_PATH, "-S", s.cli_session_id]`，Windows 用绝对路径，非 PATH 裸命令。

### 4.10 write_custom_title（G7）
- 补 `kimi_sessions.write_custom_title(session_id, title, workdir)`：改 `state.json` 的 `title`/`isCustomTitle=true`。server.py 的 `/api/sessions/{id}/rename`（api_rename_session）对 kimi 会话调用之（对齐 cbc 调用 write_custom_title 的路径）。

### 4.11 模型默认值（测试模型）
- 用户指定 kimi-k2.6 为测试模型。supported_models 自动包含 `moonshot-cn/kimi-k2.6`（config.toml 解析）。**测试时所有会话显式 `-m moonshot-cn/kimi-k2.6`**。不改默认 model（保持 config.json 可覆盖）。

## 5. 任务分解

> **2026-08-27 核对**：T1/T2/T3 各项均已实现并合入 main（commit `1c75130` E2E verified 及后续修复），下方勾选已按代码现状回填。§6 的 opencode 适配也已完成（见 `docs/design/opencode-adaptation.md`）。

### T1 差距补齐（实现 worker，hy3）
- [x] adapter.py：extract_model（§4.4）
- [x] adapter.py：enrich_after_result（§4.3，含增量游标）
- [x] adapter.py：takeover_command 用 _KIMI_PATH（§4.9）
- [x] adapter.py：supports_resume=True + 注释澄清（§4.8）
- [x] adapter.py：permission_mode_args 实测决定（§4.6）
- [x] adapter.py：mcp_args（§4.5 方案 C，KIMI_CODE_HOME 隔离 + data/kimi-homes 统一管理）；write_kimi_mcp_json 保留
- [x] wrapper.py：session_id 兜底提取、stderr 透传、健壮性（§4.7）
- [x] sessions.py：write_custom_title（§4.10）
- [x] server.py：rename 端点对 kimi 调用 write_custom_title（§4.10）
- [x] config.py：kimi 段确认（model 默认值 moonshot-cn/kimi-k2.6 等）

### T2 前端适配（同一 worker 或独立 worker）
- [x] React 前端：kimi adapter 的 settings 面板（supportedSettings 驱动，已通用）
- [x] 当时的旧前端：已同步（流式事件渲染随 `render kimi streaming events` commit 修复）；该前端现已退役，记录仅供历史追溯。
- [x] import 对话框 kimi 入口确认（/api/kimi/* 已存在）

### T3 测试验证（同一 worker，kimi-k2.6）
- [x] stream spawn：create session（adapter=kimi, model=moonshot-cn/kimi-k2.6）→ spawn → 发送任务 → 收 result
- [x] resume：第二轮任务带 cli_session_id，确认上下文延续
- [x] fork：branch 会话，确认目录复制 + 新 session_id
- [x] enrich：确认 raw_usage 累加、total_usage 更新
- [x] import：/api/kimi/sessions/import 导入真实 kimi 会话
- [x] stream-json 输出结构实测（model 字段存在性，§4.4）
- [x] 测试在 worktree 内独立端口进行，不碰运行中服务（tests/test_kimi_adapter.py 已随 main 维护）

## 6. 后续升级项（非本轮）

### ACP 接入（kimi acp）
- kimi 官方 stdio 长驻协议（JSON-RPC 2.0），支持会话恢复/工具调用通知。相比 wrapper 方案：原生事件流、会话管理更贴近 cbc。
- 需独立调研：ACP 消息格式、resume 语义、MCP 在 acp 模式下的行为、Pan adapter 协议如何映射 ACP 事件。
- 产出：`docs/design/kimi-acp-adaptation.md` 后另起一轮。

### opencode 适配
- **已完成（2026-08 合入 main）**，产出 `docs/design/opencode-adaptation.md`。

## 7. 约束与边界
- 只改本 worktree（feat/cli-adapters 分支），不动 D:/project/Pan。
- 不碰运行中服务（8768/8080/NapCat），测试用独立端口。
- 依赖 kimi CLI 的验证在 worktree 内进行；kimi -p 会消耗 token，测试用最短 prompt 控制成本。
- commit 可自行提交（本分支），push 等用户指示。

## 8. kimi 2.3.1 复核与加固（2026-10-24，T-KIMI-001）

> 环境：kimi 2.3.1（`C:/Users/14709/.kimi-code/bin/kimi.exe`），provider
> `moonshot-cn`（`https://api.moonshot.cn/v1`），模型 k2.6 / k2.7-code 均在
> `~/.kimi-code/config.toml` 中。复核方式：真实 CLI 进程 + 本地
> OpenAI 兼容端点驱动脚本化模型（Moonshot 账户余额不足，429
> `insufficient balance`，真实 token 消费被阻塞——见 §8.5）。

### 8.1 实测成立（真实 kimi 进程、真实 argv、真实事件流）

| 项 | 证据 |
|---|---|
| `-p/--prompt` + `--output-format stream-json` | 首轮输出 `meta/system.version(2.3.1)` → `assistant` → `meta/session.resume_hint(session_id)` |
| `-S <id>` resume | 二轮请求体中携带一轮的 user prompt 与 assistant 回复；中文回答正确；`resume_hint` 另广告 `kimi -r <id>`（等价入口，`--help` 未列出） |
| `-m <model>` / `--agent-file <md>` | wrapper 首轮 argv 全部被 2.3.1 接受；`--agent-file` 与 `-S` 互斥（wrapper 只在无 session_id 时传，正确） |
| wrapper 全链路 | 真实 spawn → stdin `{"text":...}` → `-p/-m/--agent-file` → `-S` 二轮 → 合成 result；session_id 从 resume_hint 捕获 |
| tool 事件 | `role:"assistant"` + `tool_calls`（Bash）；**2.3.1 新增 `role:"tool"` 结果事件**（`tool_call_id` + `content`，MCP 结果附 `<mcp-result-extras>`）；两者均被现有解析正确处理（tool 结果事件为瞬态，不入历史） |
| MCP（方案 C） | 隔离 home 的 `mcp.json` 被识别（wire 出现 `mcp.tools_discovered`；请求 tools 列表出现 `mcp__pan-probe__pan_probe`），工具被真实调用并写 marker |
| 原生 fork | `kimi fork <id> --cwd <wd> -y` → `Forked to session_xxx`；新会话自带 `title:"Fork: <parent>"`、`forkedFrom`、`titleKind`，并进入 `sessions/.index-cache/scan.json` |
| 权限 | `-p` 与 `-y/--auto/--plan` 互斥（仍报 `Cannot combine --prompt with ...`）；`-p` 进程内部固定 `permission.set_mode mode=auto` |
| sessions provider | 真实 home：list 653 条 / workspaces 31；parse/usage/title 正常；enrich 游标（`usage.record.time`）增量为空时返回 None |
| CLI status | `resolved_cli_argv()` → `C:\Users\14709\.kimi-code\bin\kimi.exe`，`available=true` |

### 8.2 2.3.1 布局变化（已适配）

1. **会话索引双源**：`session_index.jsonl` 仍在真实 home 追加（653 条，含
   Pan 探针），但**新建隔离 home（方案 C）只有 `sessions/.index-cache/scan.json`**
   （每次 turn/fork 同步更新，含 ws/cwd/时间戳）。此前 `_load_session_index`
   只读 jsonl → 隔离 home 的 MCP 会话 list/parse/usage/fork 全部为空。
   **修复**：合并 jsonl + scan.json + `wd_*/session_*/state.json` 目录扫描
   （按 sessionId 去重，jsonl 优先）。
2. **state.json 时间戳类型**：2.3.1 写 epoch ms 整数（真实 home 26 条），旧版
   是 ISO 字符串（629 条）。**修复**：`list_kimi_sessions` 输出统一转 ISO。
3. **state.json 标题**：新会话无 `title` 字段（kimi 惰性生成，`titleKind`
   可替换）；只有 fork/自定义标题后才有。`get_session_title` 返回 ""，与
   `kimi session list` 不展示标题一致；`write_custom_title` 写
   `title`+`isCustomTitle` 仍有效。
4. **wire.jsonl 事件扩展**：新增 `agent.message.appended` / `llm.request` /
   `token_counting.turn_recorded` / `prompt.completed` / `agent.turn.started` /
   `agent.turn.ended` / `runtime.set_binding` / `profile.bind` /
   `plugin.session_start` / `mcp.tools_discovered` / `changeNotifier.snapshot`；
   `usage.record` 增 `usageScope`。历史解析只依赖 `context.append_message`、
   `context.append_loop_event(content.part/tool.call)`、`usage.record`，均未变。

### 8.3 本轮修复的缺陷

| # | 缺陷 | 影响 | 修复 |
|---|---|---|---|
| F1 | 隔离 home 无 `session_index.jsonl` 时索引为空 | MCP 会话 list/parse/usage/fork 全断 | `_load_session_index` 三源合并 |
| F2 | `fork_kimi_session` 恒写**真实 home** 的 jsonl（即使 `kimi_home` 已设） | 隔离 home 的 fork 在真实 home 留脏条目、且 kimi 侧不可见 | 原生 `kimi fork` 优先；回退写同一 home 的 jsonl |
| F3 | `fork_args` 自行 fork 且返回 `[]`，worker branch 路径随后再经 provider fork | **双重 fork**，每次 branch 留孤儿 kimi 会话 | `fork_args` 改为纯返回 `[]`（对齐 claude/opencode/codex） |
| F4 | branch 端点不向 provider 传 `kimi_home` | MCP 会话 branch 直接失败（父会话不在真实 home） | `provider_kwargs` 按签名透传 `kimi_home`（server branch + worker branch） |
| F5 | branch 不继承 `kimi_home_dir` | 分支会话首轮建空 HOME，`-S` 找不到 fork 出的会话 | branch 复制 `kimi_home_dir`；`_cleanup_kimi_home` 对共享 HOME 跳过清理 |
| F6 | rename 对**所有** provider 传 `kimi_home=` | cbc/codex/claude/opencode 重命名抛 TypeError，原生标题静默不写 | 同样按签名过滤 |
| F7 | state.json 时间戳混合类型（int/ISO） | 前端/排序面对混合类型 | 输出统一 ISO |
| F8 | `default_permission_mode` property 被同名类属性覆盖（死代码） | config 的 kimi.permission_mode 被静默忽略 | 删除死 property，保留恒 "" 的类属性并注释原因 |

### 8.4 能力对照（cbc/codex vs kimi，2026-10-24）

| 能力 | cbc | codex | kimi（本轮后） | 结论 |
|---|---|---|---|---|
| Terminal TUI / native control | ✅ PTY + 结构化 hook（`cbc/native_bridge.py`） | ✅ app-server native control | ❌ | **需 native 控制**（阶段 2）：kimi 无 hook，但 wire.jsonl 有 `agent.turn.started/ended`、`turn.ended`、`prompt.completed`、`turn.prompt` 可作生命周期来源；`kimi -S` 可拉起交互 TUI 但无结构化控制通道 |
| steer（mid-turn） | ✅ 原生 | ✅ native steer + receipt | ❌ | **需 native 控制**（阶段 2）：候选通道 `kimi acp`（JSON-RPC stdio）或 `kimi web`（REST/WS）；`-p` 一次性进程天然无 mid-turn 注入 |
| interrupt（中断回合） | ✅ | ✅ 原生 interrupt | △ 仅杀进程 | **受 kimi CLI 限制**：`-p` 无取消信号；现在只能 kill kimi 子进程（wrapper 随之结束）。要做优雅中断需 ACP/web 通道 |
| takeover（接管交互） | ✅ `--resume` 拉起 | ✅ `codex resume` | ✅ `kimi -S <id>`（隔离 HOME 时经 `KIMI_CODE_HOME` execv） | 已支持 |
| fork / branch | ✅ `--fork-session` | ✅ DB 行复制 | ✅ 原生 `kimi fork`（2.3.1+）/ 旧版目录复制 | 已支持（本轮修 F2/F3/F5） |
| import | ✅ | ✅ | ✅ `/api/kimi/*` + 通用 provider 端点（真实 home） | 已支持；隔离 home 的 MCP 会话不经 import（由 Pan 自管） |
| usage/enrich | ✅ rawUsage 游标 | ✅ token_count 增量 | ✅ `usage.record` time 游标 | 已支持（值依赖 provider 返回） |
| MCP | ✅ `--mcp-config` | ✅ `-c` 内联 | ✅ KIMI_CODE_HOME 隔离 home + mcp.json（2.3.1 实测调用成功） | 已支持 |
| 模型列表 | config>help>兜底 | models_cache + catalog | config.json > config.toml `[models."..."]` > 兜底（k2.6/k2.7-code） | 已支持；2.3.1 config.toml 中两模型仍有效 |
| 权限模式 | `--permission-mode` | sandbox/-c 覆盖 | ❌ 不可用（`-p` 与 `-y/--auto/--plan` 互斥，进程内固定 auto） | **受 kimi CLI 限制**；声明与实际一致（`permission_mode_args` 返回 []） |
| CLI status | ✅ | ✅ | ✅ `resolved_cli_argv` 解析到 exe | 已支持 |
| 队列行为 | 通用 worker 队列 | 通用 | 通用（stream 长驻，无 oneshot） | 已支持（无 kimi 专属分支） |

### 8.5 限制与凭据要求

- **凭据**：kimi 依赖 `~/.kimi-code/config.toml` 的 provider + api_key
  （Pan 只读拷贝进隔离 home，不写回、不打印）。模型调用会消耗账户余额；
  2026-10-24 复核时该账户返回 429 `insufficient balance`，**真实 token 消费
  被阻塞**——因此真实模型回答的 E2E 以本地 OpenAI 兼容端点驱动真实 kimi
  进程完成（进程/argv/事件/MCP 全部真实，仅模型脚本化）。
- **thinking 事件形态未在真实模型上复现**：脚本端点返回 `reasoning_content`
  时 2.3.1 未输出 `role:"thinking"` 事件（可能因 kimi 只对特定模型字段映射
  思考流）。adapter 对 `role:"thinking"`（扁平 content 或结构化块）的解析
  保持不变并有单测覆盖。
- **子代理 wire**：会话下 `agents/` 可能有非 main 代理目录；Pan 只读
  `agents/main/wire.jsonl`（与既有行为一致）。
- **`kimi -r`**：resume_hint 广告的别名，未在 `--help` 文档中列出；Pan 统一用
  `-S`（文档化入口）。
- **隔离 home 清理**：branch 出的会话共享父会话 home；删除父会话时
  `_cleanup_kimi_home` 检测共享引用并跳过删除。
