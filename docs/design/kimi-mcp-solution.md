# Kimi Code 加载 Pan MCP 的可行方案调研

> 目标：在 Pan 编排的 **非交互 `kimi -p`** 模式下，让 kimi 加载 Pan 的 MCP 工具
> （pan / pan-qq server）。本文只做调研 + 实测验证 + 方案，不改业务代码
> （仅新增 `scripts/kimi-mcp-probe/` 探针脚本，不影响运行服务）。
> 日期：2026-08-26，调研 worker：hy3。
> kimi 版本：**0.38.0**（`~/.kimi-code/bin/kimi.exe`）。

---

## 0. 结论速览（核心结论先给）

| 方案 | 是否可行 | 实测 | 对 Pan 改动量 | 推荐度 |
|---|---|---|---|---|
| **A. 用户级 `~/.kimi-code/mcp.json`** | ✅ 可行 | **实测 PASS** | 中（写/合并用户级 mcp.json） | ⭐⭐⭐ 首选 |
| **C. `KIMI_CODE_HOME` 隔离用户目录** | ✅ 可行 | **实测 PASS** | 中（每会话设隔离 HOME + 拷贝 config.toml） | ⭐⭐⭐ 首选（最干净） |
| B. `kimi acp` 协议注入 | ⚠️ 有条件 | 协议走通但 **stdio MCP 未注册** | 大（Pan MCP 需改 HTTP/SSE 传输） | ⭐ 仅当需长驻/原生事件流时 |
| D. hooks / 环境变量 / 启动参数 预信任 | ❌ 不可行 | 文档确认无此机制 | — | ✗ |
| 项目级 `.kimi-code/mcp.json`（现状） | ❌ 非交互不可用 | **实测确认被 trust 门禁拦截** | — | 仅交互/已信任场景保留 |

**一句话结论**：在 `-p` 非交互模式下，kimi 的 **项目级** MCP 受 folder-trust 门禁拦截（已验证）；
但 **用户级** MCP 不受信任约束——实测证明只要把 pan server 写进用户级 `mcp.json`（或等价地用
`KIMI_CODE_HOME` 指向一个含 `mcp.json` 的隔离目录），`kimi -p` 就能正常调用 pan 工具。
**推荐方案：C（`KIMI_CODE_HOME` 隔离）作为首选落地路径，A（合并用户级 mcp.json）作为轻量兜底。**

---

## 1. 背景与已知事实

- kimi 通过 `mcp.json` 配置 MCP：`~/.kimi-code/mcp.json`（用户级）、`<workdir>/.kimi-code/mcp.json`（项目级）；
  同名条目项目级覆盖用户级（官方文档确认）。
- Pan kimi adapter 已实现 `write_kimi_mcp_json` 写 **项目级** `mcp.json`，注入 pan/pan-qq server +
  `PAN_AGENT_SESSION_ID/TITLE`。
- **既有 E2E 结论**（`docs/design/kimi-adaptation.md` §4.5，2026-08-26 hy3 验证）：
  `kimi -p` 下项目级 mcp.json 写入正确，但 **folder-trust 门禁导致 project MCP 不注册**
  （tool blocks=0，pan 工具不可用）。根因：非交互模式无法应答信任提示。
- kimi CLI 无 `--mcp-config`、`--system-prompt`、`--input-format` 参数（`--help` 确认）。
- 官方文档根：https://moonshotai.github.io/kimi-code/ （MCP 页：/en/customization/mcp）

---

## 2. 调研方向逐条查证（标注来源）

### 方向 1：官方文档 — MCP 配置机制 / folder-trust / 无头模式
**来源**：https://moonshotai.github.io/kimi-code/en/customization/mcp 、/en/reference/kimi-command.md 、/en/release-notes/changelog.md

- **MCP 读取位置（两级，无第三级）**：
  - 用户级：`~/.kimi-code/mcp.json`（或 `$KIMI_CODE_HOME/mcp.json`），跨项目共享。
  - 项目级：`<workdir>/.kimi-code/mcp.json`，仅对当前仓库生效。
  - 优先级：同名条目项目级覆盖用户级。
- **folder-trust 机制（关键点）**：
  > "When Kimi Code finds project-level MCP servers in an untrusted folder, it shows each
  > server's transport and launch target in the workspace trust prompt … Trusting the folder
  > enables the project-level MCP servers for that workspace."
  - 信任提示默认 `Don't trust`；**仅项目级** MCP 出现在信任提示里。**文档未提及用户级 MCP 需要信任。**
  - changelog 0.36.0："Show project MCP launch targets in the workspace trust prompt, default to
    declining trust …" → 项目级 MCP 默认拒绝。
- **`-p` 无头模式**：
  > "Run a single prompt non-interactively … This mode does not open the TUI"
  > "In `-p` mode, no human approval is requested — regular tool calls are handled under the
  > `auto` permission policy …"
  - 即：`-p` 下工具默认按 `auto` 自动审批，**MCP 工具一旦注册即可被调用**；文档未说 `-p` 禁用 MCP。
  - 没有说明项目级 MCP 因信任门禁在非交互下被静默跳过——这是实测才暴露的行为。
- **结论**：文档层面对「用户级是否绕过信任」未明说，但结构上只有项目级与信任挂钩 → 指向方案 A/C。

### 方向 2：用户级 mcp.json 是否绕过 folder-trust
**来源**：方向 1 文档推断 + 下方实测（探针 01 / 03）。
- 文档未显式声明用户级免信任，但信任提示只列举「project-level MCP servers」，且信任语义是
  per-workspace（项目级）。用户级 server 绑定用户而非文件夹，**结构上不在信任门禁内**。
- **实测确认**：见 §3 探针 01（真实用户级）与 03（`KIMI_CODE_HOME` 隔离用户级），均 PASS。✅

### 方向 3：其它入口
- **`kimi doctor`**（只读诊断）：实测仅校验 `config.toml` / `tui.toml` 合法性，**不诊断 MCP / 信任**。
  对解决本问题无帮助。
- **`kimi acp`**（ACP server over stdio）：官方称「IDE 在 `session/new`/`session/load` 的
  `mcpServers` 中提供 MCP，adapter 会做 http/stdio/sse → kimi transport 转换」。
  **实测见 §3 探针 02**：协议握手成功，但 `mcpServers` 里的 **stdio** server **未被注册为工具**
  （模型只看到内置工具 + `pan` skill，并把 `pan_probe` 当成 pan skill 参数）。
  **关键证据**：`initialize` 返回的 `mcpCapabilities` 为 `{"http":true,"sse":true}`——
  **ACP 模式只转发 http / sse MCP，不支持 stdio**（v0.38.0）。
  → ACP 路径要让 pan 工具可用，Pan 必须把 MCP server 暴露为 **HTTP 或 SSE** 传输，而非当前 stdio。
- **hooks 机制**（来源 /en/customization/hooks）：21 种事件，3 种可阻断（PreToolUse/Stop/UserPromptSubmit）。
  **文档明确 hooks 不能注入工具、不能修改 MCP 配置**——只能拦截/通知/追加上下文。→ 方案 D 不可行。
- **环境变量**（来源 llms-full.txt 全量文档扫描）：与 kimi 相关的有 `KIMI_CODE_HOME`（移动用户目录）、
  `KIMI_*` 若干实验/运行时变量（cron/swarm/secondary-model/title 等）。**未发现**
  `KIMI_HOME` / `KIMI_CONFIG` / `CLICONDUCTOR_*` / 任何预信任或禁用信任的变量。
  **`KIMI_CODE_HOME` 是方案 C 的支柱**（实测可用，见 §3 探针 03）。
- **启动参数 / CLI flag**：`--help` 与参考文档中 **无** `--trust` / `--no-trust` / 预信任类参数。→ 方案 D 不可行。

### 方向 4：结论复述
可行路径 = **用户级 mcp.json（A）** 或等价的 **`KIMI_CODE_HOME` 隔离用户目录（C）**；
ACP（B）仅在 Pan MCP 改 HTTP/SSE 后可行；项目级 + 非交互不可用；hooks/环境变量/启动参数均无解。

---

## 3. 实测验证（探针脚本，全部带 timeout，最短 prompt "1+1" 级）

> 探针统一放在 `scripts/kimi-mcp-probe/`，未触碰 `~/.kimi-code` 现有配置（用户级写入后已还原）。
> 探测 server：`probe_server.py`（基于 `mcp` SDK 1.29.0 的 FastMCP，`pan_probe` 工具被调用时写
> marker 文件，独立于模型输出判定）。模型：`moonshot-cn/kimi-k2.6`。

| 探针 | 验证点 | 结果 |
|---|---|---|
| `01_user_level_mcp.py` | 真实用户级 `~/.kimi-code/mcp.json` + `kimi -p` | **PASS**：marker 创建、`PAN_MCP_OK` 出现在输出 |
| `04_project_level_contrast.py` | 同 server 放项目级（未信任文件夹） + `kimi -p` | **NOT_INVOKED**：marker 未创建（对照，证实信任门禁是变量） |
| `03_kimi_code_home.py` | `KIMI_CODE_HOME` 指向隔离目录（含 config.toml 拷贝 + mcp.json） + `kimi -p` | **PASS**：marker 创建 |
| `02_acp_mcp.py` | `kimi acp` → `initialize`→`session/new{mcpServers:stdio}`→`session/prompt` | **协议走通，但 MCP 工具未注册**：模型只调 `pan` skill；`mcpCapabilities={http,sse}`，stdio 不被 ACP 接受 |

**判定方法**：除解析 stdout 里的 `PAN_MCP_OK` 外，主要看 `pan_probe` 被调用时写入的 marker 文件
（`%TEMP%/pan_mcp_probe_*.marker`）是否存在——与模型是否「愿意」调用解耦，判定更稳。

**对照实验的价值**：01 与 04 用**完全相同**的 server 文件与 prompt，只差「位置（用户级 vs 项目级）」，
结果一个可用一个不可用 → 确证 **folder-trust 是项目级 MCP 在非交互下失效的唯一变量**，用户级天然绕过。

---

## 4. 可落地方案清单（按优先级）

### 方案 C（首选）：`KIMI_CODE_HOME` 隔离用户目录 ⭐⭐⭐
- **做法**：Pan 在 spawn `kimi -p` 前，准备一个**每会话独立**的目录 `H`（如
  `data/kimi-homes/<sid>/`），把用户的 `config.toml`（含 provider + api_key）拷贝进 `H/config.toml`，
  并在 `H/mcp.json` 写入 pan/pan-qq server（复用 `build_mcp_servers`），然后以
  `env={"KIMI_CODE_HOME": H}` 启动 kimi。
- **可行性**：✅ 实测 PASS（探针 03）。`KIMI_CODE_HOME` 把整个用户目录重定向到 `H`，其中
  `mcp.json` 即「用户级」→ 不受 folder-trust 约束；`config.toml` 保证认证可用。
- **用户需做什么**：无（Pan 自动管理隔离目录；可选：允许 Pan 读取其 `config.toml` 以拷贝认证信息）。
- **对 Pan 代码影响**：中。改动集中在 kimi adapter 的 `build_spawn_args` / `mcp_args`：
  1. 新增 `_kimi_home_for(s)` 生成/缓存每会话 `H`；
  2. 拷贝 `REAL_HOME/config.toml`（及 `credentials/` 若 api_key 不在 inline）到 `H`；
  3. 用 `write_mcp_json(H/mcp.json, s)` 替代/补充现在的项目级写入；
  4. 在子进程 env 注入 `KIMI_CODE_HOME=H`。
  `write_kimi_mcp_json`（项目级）保留给交互/已信任场景，但 `-p` 路径改走 `H`。
- **风险**：低。隔离目录不污染用户真实 `~/.kimi-code`；多会话互不干扰（天然解决并发会话同名
  server 串味问题）。注意：会话结束后可清理 `H`（但不强求，kimi 把它当普通用户目录）。
- **额外收益**：原来担心的「用户级 mcp.json 全局共享导致多会话串味」被彻底回避。

### 方案 A（轻量兜底）：合并真实用户级 `~/.kimi-code/mcp.json` ⭐⭐⭐
- **做法**：Pan 在 spawn 前**读取并合并**现有 `~/.kimi-code/mcp.json` 的 `mcpServers`，加入
  pan/pan-qq（带本会话 `PAN_AGENT_SESSION_ID/TITLE`），写入；会话结束/清理时移除 Pan 注入的条目。
- **可行性**：✅ 实测 PASS（探针 01，直接写用户级即生效）。
- **用户需做什么**：授权 Pan 读写其 `~/.kimi-code/mcp.json`（或仅在未配置时由 Pan 创建）。
- **对 Pan 代码影响**：中。在 `mcp_args` 中实现「读-合并-写-回滚」逻辑（幂等、加锁防并发写）。
- **风险**：中。① 全局配置，被其它 kimi 会话/用户自身配置看到；② 并发会话若都用同一用户级
  mcp.json，server 指向的 `PAN_AGENT_SESSION_ID` 是「最后写入者」——串行 spawn 无碍，并发需加锁或
  改用方案 C；③ 需要可靠的清理（异常退出也要回滚条目）。
- **适用**：比 C 更简单的兜底，或当用户不允许 Pan 拷贝 `config.toml`（方案 C 需要读取认证配置）时。

### 方案 B（后续升级）：`kimi acp` + Pan MCP 改 HTTP/SSE 传输 ⭐
- **做法**：Pan 作为 ACP client 启动 `kimi acp`，在 `session/new` 的 `mcpServers` 中提供 pan server；
  但 **传输必须是 http 或 sse**（ACP `mcpCapabilities` 仅 `{http,sse}`，实测 stdio 不被注册）。
  → 需要 Pan 的 pan/pan-qq MCP server 额外支持 HTTP/SSE 传输（如用 MCP SDK 的 `sse`/`streamable-http`
  transport 起一个本地端点，再把 URL 给 ACP）。
- **可行性**：⚠️ 协议可达，但**当前 stdio MCP 在 ACP 下不可用**（实测）；需 Pan 侧传输改造后才行。
- **用户需做什么**：无（若走本地 http/sse 端点）。
- **对 Pan 代码影响**：大。① Pan MCP server 增加 http/sse transport；② 实现 ACP client
  （`initialize`/`session/new`/`session/prompt`、读取 `session/update` 事件流）替换现有
  `-p` wrapper；③ 管理本地 MCP http 端点生命周期。
- **风险**：高（改动面广、引入长驻端点、需处理 ACP 事件→Pan worker 事件映射）。收益是原生事件流、
  更贴近 cbc 的会话管理，属「后续升级项」而非本轮 autofill 范围。
- **建议**：先以 C/A 落地让 pan 工具在非交互下可用；ACP 作为独立一轮调研（原 `kimi-adaptation.md` §6 已列）。

### 方案 D（不可行）：hooks / 环境变量 / 启动参数 预信任 ✗
- hooks：文档明确**不能注入工具 / 改 MCP 配置** → 否决。
- 环境变量 / CLI flag：全量文档扫描 + `--help` 确认**无**预信任、禁用信任、或指定 mcp.json 位置的
  变量（除 `KIMI_CODE_HOME` 重定向用户目录，已归入方案 C）。→ 否决。
- 一次交互式 `Trust this folder` 后持久化信任：可行但**只对交互/TUI 生效**，`-p` 非交互路径不触发，
  无法满足 Pan 编排场景 → 不作为主方案（仅作「项目级 mcp.json 在已信任文件夹内可用」的说明）。

---

## 5. 推荐落地路径

1. **首选方案 C（`KIMI_CODE_HOME` 隔离）**：改动集中在 kimi adapter 的 spawn 流程，干净、隔离、并发安全，
   且已实测通过。建议作为本轮实现目标。
2. **方案 A 作为轻量兜底**：当无法拷贝 `config.toml`（方案 C 的前置）时，退化为合并用户级
   `~/.kimi-code/mcp.json`。
3. **保留项目级 `write_kimi_mcp_json`** 给交互式 / 已信任 workspace 场景（现状不变），但 `_p` 非交互
   路径改走 C/A，并在前端对 kimi 会话的 `mcp_servers` 配置提示「非交互模式经用户级/KIMI_CODE_HOME 加载」。
4. **方案 B（ACP）列为后续升级**，且前置条件是 Pan MCP server 支持 HTTP/SSE 传输。

### 对 `kimi-adaptation.md` §4.5 的修正建议
- 现状 §4.5 把项目级 mcp.json 标记为「-p 不加载」并建议前端隐藏 mcp 配置。
- 修正为：`-p` 非交互仍可加载 MCP，但须走**用户级 / `KIMI_CODE_HOME`**（方案 C/A），而非项目级。
  `write_kimi_mcp_json` 保留；新增 `kimi_home` 隔离逻辑；前端提示文案随之更新。

---

## 6. 探针脚本索引（`scripts/kimi-mcp-probe/`）
- `probe_server.py` — 独立 MCP server（`pan_probe` 工具，被调用写 marker）。
- `01_user_level_mcp.py` — 方案 A 验证：真实用户级 mcp.json + `kimi -p` → PASS。
- `03_kimi_code_home.py` — 方案 C 验证：`KIMI_CODE_HOME` 隔离 + `kimi -p` → PASS。
- `04_project_level_contrast.py` — 对照：项目级（未信任） + `kimi -p` → 不加载（证实信任门禁）。
- `02_acp_mcp.py` — 方案 B 验证：`kimi acp` 握手成功但 stdio MCP 未注册（ACP 仅 http/sse）。
- 各 `*_*.log` — 对应运行的原始 stdout/stderr 转录，供复核。

> 所有探针均带 `timeout`（≤150s），最短 prompt，未改动 `~/.kimi-code` 现有配置（用户级写入后已还原），
> 未触碰运行中服务（8768/8080/NapCat），未重启 kimi 进程。

---

## 7. 实现状态（2026-08-26，hy3 已落地方案 C）

**代码改动（已通过 `py_compile` + worker 级集成测试）**：

| 文件 | 改动 |
|---|---|
| `packages/core/adapters/kimi/adapter.py` | 新增 `KIMI_HOME_ROOT = SESSION_DIR.parent / "kimi-homes"`；`mcp_args` 返回 `["--kimi-home", <home>]`；新增 `_prepare_kimi_home(s)`（建 `data/kimi-homes/<sid>/`、拷贝 `config.toml`、写 `mcp.json`、回填 `s.adapter_config["kimi_home_dir"]`）；`fork_args` / `enrich_after_result` 透传 `kimi_home`；保留 `write_kimi_mcp_json`。 |
| `packages/core/adapters/kimi/wrapper.py` | `_main_loop` 增加 `kimi_home` 参数；`subprocess.Popen` 的 `env` 注入 `KIMI_CODE_HOME`；`main()` 新增 `--kimi-home` 参数。 |
| `packages/core/adapters/kimi/sessions.py` | 全部读取函数（`parse_kimi_history`/`get_raw_usage`/`get_session_title`/`write_custom_title`/`fork_kimi_session`/`_load_session_index`）贯穿 `kimi_home` 参数；有 `kimi_home` 时跳过 workdir 过滤（HOME 已会话级隔离）。 |
| `packages/web/server.py` | 新增 `_cleanup_kimi_home(sid)`（删除 `data/kimi-homes/<sid>`）；在 `api_delete_session` / `api_batch_delete_sessions` 内调用；`api_rename_session` 透传 `kimi_home`。 |

**验证**：
- 单元/准备测试 `05_home_prep_check.py`：HOME 创建、config.toml 拷贝、mcp.json 含 `pan` + 注入字段、无 MCP 会话返回 `None`（不加 `--kimi-home`）→ PASS。
- worker 级集成测试 `06_integration_wrapper.py`：完整 `build_spawn_args → wrapper → kimi -p → pan_probe` 链路 → `tool_seen_in_stream=True, marker_created=True` → **PASS（RESULT_INTEGRATION: PASS）**。

**未做（本轮范围外）**：
- 方案 A（合并用户级 mcp.json）未作为代码落地，仅保留为设计兜底；当前实现仅方案 C。
- HTTP API 全链路 sanity（独立端口起 Pan server 走 `/api/sessions`+`/api/spawn`+`/api/task` 确认 history tool blocks）未跑——worker 级集成已覆盖同一链路，API 层仅薄封装，风险低。
- ~~前端对 kimi 会话 `mcp_servers` 的提示文案更新（§5 第 3 点）~~：**已完成（commit `99e4619`，2026-08-27 核对）**。

**commit**：`feat(kimi): MCP via KIMI_CODE_HOME 隔离 + data 统一管理`。
