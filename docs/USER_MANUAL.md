# Pan 用户手册

> 面向「想真正把 Pan 用起来」的使用者的完整操作指南。概念速览见 README；编排冷启动手册（Meta-Agent 视角）见 `docs/skills/pan/SKILL.md`；本手册是两者之上的全功能实操参考。
>
> 适用版本：main 分支（commit aa430a0 之后）。文中端口默认 main 分支 8768（test 分支为 8767）。

## 目录

1. [什么是 Pan](#1-什么是-pan)
2. [安装与启动](#2-安装与启动)
3. [快速上手](#3-快速上手)
4. [核心操作详解](#4-核心操作详解)
5. [编排实践（Meta-Agent 指南）](#5-编排实践meta-agent-指南)
6. [MCP 工具层](#6-mcp-工具层)
7. [HTTP/WS API](#7-httpws-api)
8. [配置参考](#8-配置参考)
9. [多 CLI 适配](#9-多-cli-适配)
10. [前端使用](#10-前端使用)
11. [通道：Web / QQ / Remote](#11-通道web--qq--remote)
12. [故障排查](#12-故障排查)
13. [安全与运维提示](#13-安全与运维提示)

---

## 1. 什么是 Pan

Pan 是一个 **CLI Agent 编排调度平台**（orchestrator）：Supervisor/Worker 架构下，一个「Meta-Agent 主管」（又称 SMA，Super Meta Agent）通过 MCP（Model Context Protocol）工具与 WebSocket（WS）事件流，同时指挥多个 Worker 并行干活。传统 AI 编程助手是一对一对话；Pan 是**一对多**——你只跟一个主管对话，它拆解任务并调度一整支 CLI Agent 工人团队。

### 1.1 定位光谱

从最小用法到最全用法：

| 层级 | 用法 | 涉及组件 |
|------|------|----------|
| 最小 | 一个入口管理多个 CLI 会话：建 Session、派任务、看结果 | Web Dashboard / HTTP API |
| 进阶 | 一个 Meta-Agent 编排多个 Worker 并行（fan-out） | MCP 工具 + report_subscribe |
| 完整 | 外部 Agent 集群协作 + 多渠道指挥（Web/QQ/公网）+ 记忆/人设 | 全部模块 |

### 1.2 核心概念一览

| 概念 | 英文 | 说明 |
|------|------|------|
| Agent / Session | Session | **逻辑编排对象**：持久身份（`ses_<16hex>`），拥有收件箱（`queue_pending`）、层级（agentLevel）、管理链（managedBy）。投递/编排语义都绑在它上面，独立于 Worker 生命周期 |
| Worker | Worker | **物理执行体**：临时的 CLI 进程实例（cbc/kimi/…），属于某 Agent，可随时 kill/重建。「进程是顺带的」 |
| Meta-Agent / SMA | Meta-Agent | 主管角色：不亲自干活，只拆解、派活、听汇报、验收。任何能发指令（MCP/HTTP）+ 能收情报（报告订阅/WS）+ 有身份（`PAN_AGENT_SESSION_ID`）的一方都可担任 |
| Adapter | CLI Adapter | 每种 CLI Agent 一个协议化适配器：`cbc` / `kimi` / `opencode` / `claude` / `codex` |
| Memory | 记忆 | 向量 + 全文（SQLite FTS5）混合检索，开工前自动注入相关记忆 |
| Character | 人设 | 角色 + 独立记忆库（`char_<16hex>`），跨 Session 保持同一身份 |
| Watchdog | 看门狗 | 每个 Worker 一只：卡死/静默/空闲超时自动清理；全局级还能对「队列非空但无活 Worker」的 Session 自动补员 |

数据模型要点：**Worker 无记忆，Session 有记忆**。每次 spawn 都是全新进程；Session 保存 `history` 与 `cliSessionId`，重建 Worker 时 adapter 用 `--resume` 从 CLI 原生 transcript 恢复完整上下文。

---

## 2. 安装与启动

### 2.1 前置要求

- Python 3.14（开发环境为 3.14.5）
- Node.js + npm（编译 legacy 前端）；React 前端需 pnpm
- 至少一个受支持的 CLI：`cbc`（CodeBuddy CLI）、`kimi`、`opencode`、`claude`、`codex`

### 2.2 安装步骤

```bash
# 1. 安装最小依赖（仅核心，不含 Memory 的 ML 链）
pip install -r minimal-requirements.txt

# 2. 生成配置（所有字段可选，省略用默认值）
cp config.example.json config.json        # Windows: copy config.example.json config.json

# 3. 编译 legacy 前端（TS 源码 → static/js/app.js，必须在项目根执行）
npx tsc

# 4.（可选）构建 React 前端
cd packages/web && pnpm install && pnpm build && cd ../..

# 5. 启动
python main.py
# → http://127.0.0.1:8768
```

`scripts/` 下另有免手动步骤的脚本：`setup.bat` / `setup.sh`（安装依赖、生成 config.json、探测 QQ 解释器等）、`start_pan.bat` / `start.sh`（启动）、`stop_pan.bat` / `stop.sh`（停止；Windows 版按 PID 文件做精确进程树杀，不误伤其他 python 进程）。`git config core.hooksPath scripts` 后 `scripts/pre-commit` 会同时校验前端双源。

### 2.3 端口与环境变量

| 端口 | 用途 |
|------|------|
| 8768 | Pan 主服务（main 分支默认；test 分支 8767；`config.json` 的 `port` 字段） |
| 8769 | Remote 状态服务（`remote.status_port`） |
| 8080 | QQ 插件（NoneBot）HTTP API，不对外 |
| 3001 / 3002 | NapCat / LLOneBot 网关（正向 WS） |
| 9740 | MCP server SSE/streamable-http 模式默认端口 |

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `PAN_PORT` | — | 覆盖 `port` |
| `PAN_HOST` | `127.0.0.1` | 监听地址（非 loopback 会打印无鉴权告警） |
| `PAN_API_URL` | `http://127.0.0.1:8768` | MCP server 连接 Pan Core 的地址 |
| `PAN_URL` | `http://127.0.0.1:{port}` | QQ Bridge 访问 Pan Core 的地址 |
| `PAN_QQ_API_URL` | `http://127.0.0.1:8080` | pan-qq MCP 连接 QQ 插件的地址 |
| `PAN_QQ_PYTHON` | 平台默认 | QQ bot 解释器路径 |
| `PAN_QQ_MODE` | — | 覆盖 `qq.mode` |
| `ONEBOT_WS_URLS` / `ONEBOT_ACCESS_TOKEN` | — | QQ 通道 WS 地址 / token（可写在 `packages/qq/.env`） |

### 2.4 停止

Ctrl+C 优雅退出（QQ bot 子进程随之终止）；或用 `scripts/stop_pan.bat` / `stop.sh`。

---

## 3. 快速上手

以 HTTP API 为例走通主链路（等价的 MCP 工具链路见 §6）。

```bash
BASE=http://127.0.0.1:8768

# 1. 创建 Session（只建会话，不启动 Worker）
curl -X POST $BASE/api/sessions -H "Content-Type: application/json" \
  -d '{"name":"fix-h1","adapter":"cbc","model":"hy3"}'
# → 完整 session 对象，记下返回的 "id"（ses_...）与 "workdir"

# 2. 异步派发任务（无活 Worker 时自动 spawn，立即返回）
curl -X POST $BASE/api/assign -H "Content-Type: application/json" \
  -d '{"sessionId":"ses_xxxx","text":"修复 utils.py 中的空指针，跑通测试"}'
# → {"status":"queued","workerId":"worker-1","sessionId":"ses_xxxx"}

# 3. 查看结果（轮询 lastResult.status：queued → running → done/error）
curl $BASE/api/sessions/ses_xxxx

# 4. 收尾：删除 Session（同时 kill Worker；注意不删 workdir 磁盘目录）
curl -X DELETE $BASE/api/sessions/ses_xxxx
```

> **Windows curl 注意**：内联中文 body 会因终端编码（GBK）报 `{"detail":"There was an error parsing the body"}`。中文任务一律 `--data-binary @body.json`（UTF-8 保存）或用 python requests。

订阅制报告（推荐给编排者）：让 Meta-Agent 订阅目标 Session，完成后报告自动投进它的落盘收件箱 `queue_pending`，无需轮询——

```bash
curl -X POST $BASE/api/report-subscribe -H "Content-Type: application/json" \
  -d '{"managerId":"ses_manager","sessionId":"ses_xxxx"}'
```

---

## 4. 核心操作详解

### 4.1 派活三式：assign / send / send_force

| 操作 | HTTP | MCP | 语义 | 适用 |
|------|------|-----|------|------|
| assign | `POST /api/assign` | `agent_assign(session_id, text, task_id?)` | **异步派新任务**：立即返回 queued，无活 Worker 自动 spawn；`taskId` 幂等（同 id 重发不双跑：已完成回缓存结果、进行中回 `pending`） | 新任务 / 并行 fan-out（**默认首选**） |
| send | `POST /api/send` | `agent_send(session_id, text)` | 向已有 Agent 发消息：**排队不打断**，目标空闲才处理；无活 Worker 不报错，入持久队列（返回 `pendingSpawn: true`），全局 watchdog 自动 spawn 后分发 | 多轮追问 / 补充线索 |
| send_force | `POST /api/send` + `"force":true` | `agent_send_force(session_id, text)` | **强制推送 = restart + send**：打断进行中任务立即送达；无活 Worker 时退化为入队 | 方向变更 / 紧急指令 / Worker 卡死兜底 |

Pan 内 Session 互发消息自动加身份前缀 `////by agent : ses_xxx | 标题`，目标 Worker 据此区分编排消息与真实用户消息（`agent_assign` 不加前缀）。

### 4.2 生命周期操作

| 操作 | HTTP | MCP | 说明 |
|------|------|-----|------|
| spawn | `POST /api/spawn` | `agent_spawn` | 启动 Worker；已有 Worker 先 kill（一个 Agent 同时只有一个 Worker） |
| kill | `POST /api/kill/{worker_id}` | `agent_kill` | 杀 Worker 进程树，Session 数据保留；无活 Worker 时无害 no-op |
| restart | `POST /api/worker/{worker_id}/restart` | —（send_force 内部走此端点） | 杀进程后带 resume 重建 |
| interrupt | `POST /api/worker/{worker_id}/interrupt` | — | 中断当前任务（仅 running 时） |

### 4.3 归属关系：claim / unclaim

- `POST /api/claim`（MCP `session_claim`）：建立 manager ↔ session 双向 managed 关系，**claim 自动 report_subscribe**；目标已被他人管理则拒绝；幂等。批量版 `session_claim_many` / `session_unclaim_many` 逐项隔离、部分成功。
- `POST /api/unclaim`（MCP `session_unclaim`）：解除 managed 关系并自动退订报告；仅当前 manager 可解除。
- 每个 Session 只属于一个主管（星形拓扑）；`GET /api/sessions/{id}/managers`（MCP `manager_chain`）可查上级管理链。

### 4.4 branch（fork 分支）

`POST /api/sessions/{id}/branch`，body `{"name": "fork-name"}`。从现有 Session 分支：纯文件操作 fork adapter 原生 transcript（cbc 复制 JSONL / kimi 复制目录 / opencode 复制 SQLite 行），新 Session 继承设置 + MCP 绑定，与原会话互不影响。**无 MCP 工具，需 HTTP 直调**。运行中 Worker 也可 `POST /api/worker/{worker_id}/branch`（CLI `--fork-session` 方式）。

### 4.5 takeover（人类接管）

`POST /api/worker/{worker_id}/takeover`：restart Worker 后在新终端窗口打开 adapter 原生交互式 CLI（`--resume`），Worker 状态置 `held`。held 期间任务投递被拒、watchdog 跳过。`GET /api/worker/{worker_id}/takeover-command` 只返回命令不执行（移动端复制用）。恢复：restart 清 held。

### 4.6 handoff（替身交接）

`POST /api/sessions/{id}/handoff`（MCP `session_handoff`）。场景：上下文过大需精简，或中途切换 adapter（普通 Session 不能直接换 adapter）。行为：

1. 创建孪生 Session B 接替 A：接管 A 的全部关系网（managed、report_subscriptions、QQ 绑定）；
2. B 自动 manage A，A 重命名为 `(archive) <原名>` 归档可读；
3. `copySettings=true`（默认）1:1 复制设置（不含 system_prompt，`cli_session_id` 清空——精简上下文的关键）；`false` 时必须显式传 `adapter`；
4. B 的 system_prompt = `handoffPrompt`（必填，由 A 的 agent 编写交接简报）与 A 原 system_prompt 拼接。

### 4.7 批量删除与清理

- `POST /api/sessions/batch-delete`，body `{"sessionIds": [...]}`（MCP `session_batch_delete`）：批量删除并清理跨 Session 引用。
- `session_delete` / batch 都**不删 workdir 磁盘目录**，需自行清理。
- 复用已删除的 Session：底层 CLI 会话（`~/.codebuddy/projects/` 等）仍保留，可用导入端点（§7.4）按 `cli_session_id` 重新导入，恢复全部历史上下文。

### 4.8 QQ 订阅

`POST /api/qq/subscribe`，body `{"sessionId","target_type":"user"|"group","target_id"}`（MCP `session_qq_subscribe`）。订阅后该 QQ 会话新消息进 inbox 时，向 Session 的 `queue_pending` 推 `@@@@by qq` 提醒并唤醒 Worker。退订 `POST /api/qq/unsubscribe`。

### 4.9 任务文本怎么写（派发判定）

派活前先 `session_get` 查 **`cliSessionId`**：

| `cliSessionId` | 含义 | 任务文本写法 |
|----------------|------|--------------|
| 非空 | Worker 会 `--resume` 恢复完整上下文 | **简短指令**：追加任务/恢复中断/串行下一步即可，不要重发完整任务描述 |
| 空/null | 全新会话，Worker 无上下文 | **自包含描述**：背景/目标/涉及文件（相对 workdir）/边界/验收标准 |

---

## 5. 编排实践（Meta-Agent 指南）

### 5.1 决策三问

派发前自问：① 能真并行吗？② 拆了更快吗？③ 精度关键吗？任一不过 → 自己做；全过 → 并行派发。

### 5.2 并行 fan-out 主链路

```
session_create → report_subscribe（订阅）→ agent_assign × N → queue_pending 收报告 → session_get 汇总 → session_delete 收尾
```

- `agent_assign` 立即返回 queued，**不需要手动轮询**；
- 完成通知只有一条编排路径：MCP `report_subscribe` → 报告落盘到 meta-agent 的 `queue_pending`（跨服务重启不丢）；
- 外部 WS 盯梢（`/ws/agent`、`packages/mcp/monitor_workers.py`）仅供测试/排障/外部协调者；
- 传 `task_id`（uuid 样幂等键）保证重试不双跑；
- zombie 通知：被管 Session 的 Worker 在任务进行中异常死亡时，收 `{"status":"error","type":"zombie",...}` 报告（正常完成后的 idle 回收不报）。

### 5.3 trust-but-verify 验收

合并汇报前逐项核对改动、跑测试验证。读结果：`session_get(session_id)` 的 `lastResult.status`（`queued`/`running`/`done`/`error`/`pending`）与 `result` 字段。

### 5.4 worktree 并行

多个 Worker 共改一个项目时，让所有 Session 的 `workdir` 用**绝对路径**指向同一项目目录（或各自独立 git worktree），避免提交冲突。`workdir` 默认 `data/workdirs/<name>`（相对基准 = 实际运行的那个 Pan 服务实例的数据根，以 `session_create` 返回的 `workdir` 字段为准）。

### 5.5 串行依赖

阻塞式 handoff 已移除（2026-08-26）。串行 = 派发后订阅报告，报告入 `queue_pending` 即「下一步」的信号——「等」是 meta-agent 的 idle 状态，而非阻塞调用。

### 5.6 清理

完成后 `session_delete` / `session_batch_delete` 释放进程与磁盘；watchdog 只回收进程不删 Session。不再需要的会话及时清理。

---

## 6. MCP 工具层

### 6.1 接入方式

**方式 A：Session 内自动注入（推荐）**——创建 Session 时指定 `mcpServers: ["pan"]`（或用 SMA 等自带 MCP 的模板），adapter 在 spawn 时自动生成 `data/mcp-configs/<session_id>.mcp.json` 并经 `--mcp-config` 注入，同时写入 `PAN_AGENT_SESSION_ID` / `PAN_AGENT_SESSION_TITLE` 环境变量（工具据此识别调用方身份）。各 adapter 注入方式：cbc/claude 写 `--mcp-config`；kimi 写会话级隔离 home（`--kimi-home`）；opencode 写项目级 `opencode.json`；codex `-c mcp_servers.*` 内联注入。

**方式 B：独立进程接入（任意 MCP 客户端）**：

```bash
# stdio（本地 CLI 客户端，在 .mcp.json / --mcp-config 里声明 command）
PAN_API_URL=http://127.0.0.1:8768 python -m packages.mcp.server --transport stdio

# SSE / streamable-http（远程或多客户端，默认端口 9740，路径 /sse）
python -m packages.mcp.server --transport sse --port 9740
```

后端地址优先级：`--pan-url` 参数 > `PAN_API_URL` 环境变量 > `http://127.0.0.1:8768`。独立进程没有 `PAN_AGENT_SESSION_ID`，依赖身份的工具（claim / report_subscribe / manager_chain 等）不可用。

> **三对齐**：MCP server 目标端口（`PAN_API_URL`）必须与 `PAN_AGENT_SESSION_ID` 所在 Pan 实例同端口，否则 `report_subscribe` / `qq_bind` 失效。

### 6.2 `pan` server 工具清单（35 个）

命名分层：`agent_*` 是一等工具（以 session_id 寻址，无活进程也容忍）；`worker_*` 是兼容别名（DEPRECATED），仅 `worker_id` 进程寻址为遗留独有路径，新代码一律用 `agent_*`。

**会话管理（15）**

| 工具 | 关键参数 | 说明 |
|------|----------|------|
| `session_create` | `name`（必填，唯一），`adapter?`/`model?`/`permission_mode?`/`workdir?`/`session_template?`/`character_id?`/`system_prompt?`/`pan_access?` | 创建会话（不 spawn）；workdir 默认 `data/workdirs/<name>`，Pan 外用绝对路径 |
| `session_import` | `action`（`list_projects`/`list_workspaces`/`list_sessions`/`import`），`adapter?`，`cwd?`/`project_dir?`，`session_id?` | 导入外部 CLI 历史会话（cbc/kimi/opencode…）；仅建 Session 不 spawn |
| `session_list` | `summary?` | 列出全部会话；`summary=true` 只返回精简字段（巡检首选，避免全量 history 撑爆输出） |
| `session_managed` | — | 调用者管理的 session 摘要（需 `PAN_AGENT_SESSION_ID`） |
| `manager_chain` | — | 调用方的上级 manager 链 |
| `session_get` | `session_id`，`limit?` | 详情（history + lastResult） |
| `session_update` | `session_id`，`model?`/`permission_mode?`/`always_thinking_enabled?`/`effort?`/`max_thinking_tokens?`/`mcp_servers?`/`game_id?` | PATCH 封装；改 mcp_servers 返回 `requireRestart: true`（idle worker 自动 respawn 生效） |
| `session_delete` | `session_id` | 删除并 kill worker（不删 workdir） |
| `session_batch_delete` | `session_ids` | 批量删除（逐个过 managed 隔离检查） |
| `session_handoff` | `session_id`，`handoff_prompt`（必填），`copy_settings?`(=true)，`adapter?`/`model?`/`permission_mode?` | 替身交接（§4.6） |
| `session_claim` / `session_claim_many` | `session_id` / `session_ids` | 认领（自动 report_subscribe；被他人管理则拒绝） |
| `session_unclaim` / `session_unclaim_many` | 同上 | 解除 managed（自动退订） |
| `session_history` | `session_id`，`limit?=50`，`before?` | 分页历史 |

**Agent 编排（7，一等工具）**

| 工具 | 参数 | 说明 |
|------|------|------|
| `agent_spawn` | `session_id`，`adapter?`，`model?` | 生成 Worker；已有先 kill；spawn 即接管（自动 claim） |
| `agent_task` | `session_id`，`text`，`source?` | 发任务；无活 Worker 自动 spawn |
| `agent_assign` | `session_id`，`text`，`task_id?` | **异步派发**（新任务默认首选），taskId 幂等 |
| `agent_send` | `session_id`，`text` | 排队不打断；无活 Worker 入持久队列 |
| `agent_send_force` | `session_id`，`text` | restart + send，立即生效 |
| `agent_kill` | `session_id` | 杀 Worker（数据保留；无活 Worker 无害 no-op） |
| `agent_list` | `summary?` | `session_list` 的别名 |

**Worker 兼容别名（7，DEPRECATED）**：`worker_spawn` / `worker_task` / `worker_assign` / `worker_send` / `worker_send_force` / `worker_kill` / `worker_list`——内部委托 `agent_*` 同一实现；`worker_id` 进程寻址为遗留路径。

**订阅 / QQ / 其他（6）**

| 工具 | 参数 | 说明 |
|------|------|------|
| `report_subscribe` | `session_id` | 订阅完成报告（**订阅即接管**，自动 claim） |
| `report_unsubscribe` | `session_id` | 退订（仅自己管理的 session） |
| `session_qq_subscribe` / `session_qq_unsubscribe` | `target_type`（`"user"`/`"group"`），`target_id` | 订阅/退订 QQ inbox 提醒（`@@@@by qq` 入收件箱） |
| `model_list` | `adapter?` | 列出 adapter 可用模型 |
| `pan_handbook` | — | 返回 `docs/skills/pan/SKILL.md` 全文（冷启动先调它） |

### 6.3 `pan-qq` server 工具（6 个，`packages/qq/mcp.py`）

`qq_send_message` / `qq_read_conversation` / `qq_read_inbox` / `qq_list_contacts` / `qq_bind` / `qq_unbind`。selective 模式下 meta-agent 用它做 QQ 选择性收发；`qq_bind` 后新消息以 `@@@@by qq` 提醒推入 `queue_pending`。SMA 模板已默认挂载。

### 6.4 安全模型（MCP 层）

无传统鉴权，靠「身份注入 + managed 隔离」：Session 的 `pan_access` 三能力位 `restrict_to_managed` / `can_claim_unmanaged` / `auto_claim_created`（默认全 False）。受限调用方操作他人 Session 会被 `permission_denied`；spawn/task/assign/send 自带「派任务即接管」。注意这些限制**只在 MCP 层实施**，HTTP API 不检查（见 §13）。

---

## 7. HTTP/WS API

基址 `http://127.0.0.1:<port>`；全部返回 JSON，失败多为 HTTP 200 + `{"error": "..."}`。完整 69 端点清单见 README「API 概览」；本章给主要端点与调用示例。**请求 body 用 camelCase，MCP 参数用 snake_case**（如 HTTP `sessionId` ↔ MCP `session_id`；创建响应里叫 `id`，后续请求体一律用 `sessionId`）。

### 7.1 Session 管理

| 方法+路径 | 用途 |
|-----------|------|
| `GET /api/sessions`（`?summary=1`） | 列出全部（summary 精简；全量 history 截最后 50 条） |
| `POST /api/sessions` | 创建（body：`name`/`adapter`/`model`/`permissionMode`/`workdir`/`sessionTemplate`/`systemPrompt`/`alwaysThinkingEnabled`/`effort`/`maxThinkingTokens`/`outputMode`/`panAccess`/`characterId` 等，均可省略） |
| `GET /api/sessions/{id}` | 详情（`lastResult`/`workerStatus`/`managedBy`/`reportSubscriptions` 等） |
| `GET /api/sessions/{id}/history?limit=50&before=<游标>` | 历史分页 |
| `PATCH /api/sessions/{id}` | 更新设置（model/effort/MCP 等；idle Worker 自动 respawn，running 标 `pending_restart`） |
| `POST /api/sessions/{id}/rename` | 重命名（body `{"name"}`；同步回写 adapter 原生存储） |
| `POST /api/sessions/{id}/branch` | fork 分支（§4.4） |
| `POST /api/sessions/{id}/handoff` | 替身交接（§4.6） |
| `DELETE /api/sessions/{id}` / `POST /api/sessions/batch-delete` | 删除 / 批量删除 |
| `GET /api/sessions/{id}/managers` | manager 链 |

### 7.2 Worker 与任务投递

```bash
# spawn（sessionId 必填；已有 Worker 先 kill）
curl -X POST $BASE/api/spawn -d '{"sessionId":"ses_xxxx"}'
# 发任务（workerId 或 sessionId 寻址；无活 Worker 自动 spawn）
curl -X POST $BASE/api/task -d '{"sessionId":"ses_xxxx","text":"..."}'
# 列出运行中的 Worker
curl $BASE/api/list
# 杀 Worker
curl -X POST $BASE/api/kill/worker-1
```

其余：`POST /api/worker/{id}/restart|settings|rename|branch|interrupt|takeover`、`GET /api/worker/{id}/takeover-command`。

### 7.3 编排端点

`POST /api/assign`、`POST /api/send`（`force:true` 即强制）、`POST /api/claim` / `POST /api/unclaim`（body `{"managerId","sessionId"}`）、`POST /api/report-subscribe` / `POST /api/report-unsubscribe`——语义见 §4/§5。

### 7.4 导入 / 设置 / Manifest / Memory / 文件

| 类别 | 端点 |
|------|------|
| 通用导入 | `GET /api/adapters/{adapter}/sessions`、`POST /api/adapters/{adapter}/sessions/import`（claude/codex 走此端点） |
| cbc/kimi/opencode 导入 | `GET /api/cbc/projects`、`GET /api/cbc/sessions`、`GET /api/cbc/browse`、`POST /api/cbc/sessions/import`；`GET /api/kimi/workspaces`、`GET /api/kimi/sessions`、`POST /api/kimi/sessions/import`；`GET /api/opencode/sessions`、`POST /api/opencode/sessions/import` |
| 模型/Adapter | `GET /api/models?adapter=cbc`、`GET /api/adapter/config?adapter=cbc`、`GET /api/adapters` |
| 设置 | `GET`/`PUT /api/settings/ui`（App Settings 显示设置）；`POST /api/config/reload`（config.json 热重载，`{"scope":"adapters"\|"worker"\|"all"}`）；`POST /api/manifest/reload`（manifest 热重载） |
| 模板/MCP | `GET /api/session-templates`（`GET /api/characters/profiles` 为其废弃别名）、`GET /api/mcp/servers`、`GET /api/manifest/command-routes` |
| Character | `GET`/`POST /api/characters`、`GET`/`DELETE /api/characters/{id}` |
| Memory | `POST /api/memory/index`、`GET /api/memory/search?q=`、`GET /api/memory/stats`、`POST /api/memory/inject` |
| 文件系统 | `GET /api/fs/list`、`GET /api/fs/read`、`POST /api/fs/write`、`POST /api/fs/rename`、`POST /api/fs/delete`（限 session workdir 内，拒绝 `..` 逃逸，单文件 5 MiB 上限） |
| QQ | `POST /api/qq/subscribe`、`POST /api/qq/unsubscribe`、`POST /api/qq/notify`、`GET /api/qq/contacts` |

### 7.5 WebSocket

| 端点 | 用途 |
|------|------|
| `ws://127.0.0.1:{port}/ws` | Dashboard 通道：接收全部广播事件；客户端唯一可发 `{"type":"user_inject","sessionId":"...","text":"..."}`（发任务，无 Worker 自动 spawn） |
| `ws://127.0.0.1:{port}/ws/agent` | Meta-Agent 通道：默认只推 `worker.result`；可 subscribe 过滤 + reconnect 补发，还可直接发 task/spawn/assign/send/kill |

`/ws/agent` 客户端消息示例：

```json
{"type": "subscribe", "eventTypes": ["worker.result", "worker.zombie"], "sessionIds": ["ses_xxxx"]}
{"type": "reconnect", "sessionIds": ["ses_xxxx"]}
```

`eventTypes` 省略/空 = 默认 `["worker.result"]`；`["*"]` 订阅全部。广播事件全集：`worker.stream` / `worker.result` / `worker.status` / `worker.spawned` / `worker.crashed` / `worker.zombie` / `worker.destroyed` / `worker.restarted` / `worker.reconfigured`、`session.created` / `session.updated` / `session.renamed` / `session.deleted` / `sessions.deleted`、`error`。`worker.result` 形如：

```json
{"type": "worker.result", "workerId": "worker-1", "sessionId": "ses_xxxx",
 "status": "done", "result": "...", "taskId": "...", "taskSeq": 3}
```

---

## 8. 配置参考

配置文件 `config.json`（**gitignored**），模板 `config.example.json`；所有字段可选，默认值在 `packages/core/config.py`。改后重启生效（worker/adapters 部分支持 `POST /api/config/reload` 热重载）。

| 字段 | 默认 | 说明 |
|------|------|------|
| `port` | `8768` | 主服务端口 |
| `frontend` | `"coexist"` | `coexist`/`react`：React 接管 `/`，旧前端在 `/vanilla`；`legacy`：仅旧前端 |
| `cbc.model` | `"deepseek-v4-flash"` | cbc 默认模型 |
| `cbc.permission_mode` | `"bypassPermissions"` | 可选 `""`/`default`/`acceptEdits`/`bypassPermissions`/`plan`/`dontAsk`/`auto` |
| `cbc.always_thinking_enabled` | `false` | cbc 思考开关；false 时 `--effort` 不生效 |
| `cbc.effort` | `""` | `none`/`off`/`auto`/`low`/`medium`/`high`/`xhigh`/`max`/`ultracode`；仅思考开启时生效 |
| `cbc.models` | `[]` | 不填自动识别（`cbc --help` 解析）；填写 = 限制可用模型 |
| `kimi.model` | `"moonshot-cn/kimi-k2.6"` | kimi 默认模型 |
| `kimi.*` 其余 | 同 cbc 结构 | kimi 的 permission_mode/effort 暂不支持，保持空值 |
| `cbc_import.*` | 见模板 | 外部会话导入过滤（`min_message_count`/`max_sessions_shown`/`exclude_workdir_patterns`/`project_dir_exact_match`/`import_recent_days`/`min_resume_bytes`） |
| `worker.timeout_sec` | `300` | queued 静默超时（无 stdout 输出即 kill） |
| `worker.task_timeout_sec` | `1800` | stream running **任务运行时长**上限（长思考/大文件读取不误杀） |
| `worker.idle_sec` | `300` | idle 空闲回收（`held`/`zombie` 跳过） |
| `memory.enabled` | `true` | 记忆注入开关（character session 首次任务可能被 embedding 加载阻塞，可关） |
| `mcp.enabled_default` | 已废弃 | MCP 启用由 session 的 `mcp_servers` 非空决定 |
| `qq.enabled` | `true` | 是否启动 QQ bot（`packages/qq/bot.py`） |
| `qq.mode` | `"mirror"` | `mirror` 自动回复；`selective` 只进 inbox，由 meta-agent 经 pan-qq MCP 决策（`PAN_QQ_MODE` 可覆盖） |
| `qq.channel` | `"napcat"` | `napcat` / `llonebot`；各通道 WS 地址配 `qq.<channel>.ws_urls`，兼容旧 `qq.ws_url` |
| `qq.python` | `""` | QQ bot 独立解释器（`PAN_QQ_PYTHON` 优先） |
| `remote.*` | 见模板 | `enabled`/`provider`(cloudflare)/`quick_tunnel`/`config_path`/`binary_path`/`status_port` |
| `logging.*` | INFO / `data/logs/pan.log` | `level`/`file`/`max_bytes`(10MB)/`backup_count`(7)/`console` |
| `plugin_manifests` | `["manifest.json"]` | 外部 Character profiles / session 模板 / MCP server 清单 |
| `ui.*` | `{}` | App Settings 显示设置（前端经 `/api/settings/ui` 读写） |

---

## 9. 多 CLI 适配

Adapter 协议（`packages/core/adapters/base.py`）+ 注册表（`registry.py`）。五种内置 adapter：

| Adapter | CLI | 执行模式 | Resume/Fork | MCP 注入 | 备注 |
|---------|-----|----------|-------------|----------|------|
| `cbc` | CodeBuddy CLI | stream + oneshot（唯一双模式） | ✔ / ✔（`--fork-session`） | `--mcp-config` | 主力 adapter；原生 JSON 流协议 |
| `kimi` | Kimi CLI | stream（wrapper 长驻） | ✔ / ✔ | 会话级隔离 home（`--kimi-home`） | 思考模式由自身 config.toml 控制 |
| `opencode` | OpenCode CLI | stream（wrapper） | ✔ / ✔ | 项目级 `opencode.json` | |
| `claude` | Claude Code CLI | one-shot | ✔ | `--mcp-config` | 逐条 `claude -p --output-format stream-json` |
| `codex` | OpenAI Codex CLI | stream（wrapper） | ✔ | `-c mcp_servers.*` 内联（零文件污染） | |

执行模式：`stream` 长驻进程（消息写 stdin，可挂 MCP）；`oneshot` 每任务起一次性进程（`outputMode: "oneshot"` 时启用，仅 adapter 声明支持时可选）。特殊行为详见 `docs/references/cli-adapter-special-behaviors.md`。

---

## 10. 前端使用

双轨前端（改前端须守双源约定：legacy 源码 `packages/web/ts/app.ts` → 项目根 `npx tsc`；React 源码 `packages/web/src/` → `pnpm build`；产物均 gitignored，禁止直改）：

| URL | 内容 |
|-----|------|
| `/` | `frontend` 为 `coexist`/`react` 时 307 到 `/react/`（React Dashboard）；`legacy` 时为旧前端 |
| `/react/` | React Dashboard（`packages/web/dist/`） |
| `/vanilla` | 旧版 Vanilla JS 前端（移动 UA 自动分流 mobile.html） |

React Dashboard 功能：会话列表（含 workerStatus 分组）、实时输出围观、历史查看、模型/adapter/权限选择（创建与 PATCH）、App Settings（`/api/settings/ui`，跨浏览器共享的显示设置）、外部会话导入、文件浏览器（限 workdir）。开发模式 `cd packages/web && pnpm dev`（Vite HMR + 代理后端）。

---

## 11. 通道：Web / QQ / Remote

### 11.1 Web

主通道：Dashboard + `/ws` + HTTP API，见 §7/§10。

### 11.2 QQ（QQ Bridge）

- 依赖：`packages/qq/requirements.txt`（nonebot2 + onebot 适配器 + httpx），跑在**独立解释器**（NoneBot 不装项目 .venv；`setup.bat` 探测后写入 `qq.python`）。
- 网关：NapCat（正向 WS，端口 3001）或 LLOneBot（3002），`qq.channel` 选择；WS 地址写 `packages/qq/.env` 的 `ONEBOT_WS_URLS` 或 config 的 `qq.<channel>.ws_urls`。
- 启动：`python main.py` 按 `qq.enabled` 自动拉起/终止 QQ bot（PID 写 `data/qq_bot.pid`）；NapCat 不可达时降级运行（每 3s 重连）。
- 模式：`mirror`（收到消息自动建 Session 并回复）/ `selective`（消息只进 inbox，meta-agent 经 pan-qq MCP 处理）。
- 编排接入：`session_qq_subscribe`（§4.8）收 inbox 提醒；`manifest.json` 的 `command_routes` 可声明 QQ 前缀命令直发外部 HTTP API（不走 LLM）。

### 11.3 Remote（Cloudflare Tunnel）

```bash
python -m packages.remote        # 或 scripts/start_cf.ps1
```

`remote.quick_tunnel=true` 输出临时 `*.trycloudflare.com` URL；`false` 需 `remote.config_path` 指向 named tunnel 的 config.yml（公网域名取其 `ingress.hostname`）。状态服务 `curl http://127.0.0.1:8769/status`。隧道转发 Pan 主端口——公网侧同样**无鉴权**（§13）。

---

## 12. 故障排查

| 现象 | 原因与处理 |
|------|-----------|
| `workerStatus` 变 `null` | watchdog 已回收（idle/静默/任务超时）。直接 `agent_assign` / `agent_spawn` 自动重建并恢复上下文（Session 数据完好） |
| 任务长时间无回复 | 查 `lastResult.status`：`idle` = 已完成未读，`session_get` 即可；`running` 且超时可能已被回收。回收只杀进程不删 Session |
| 超时配置不生效 | `worker.*` 改后需重启或 `POST /api/config/reload`（scope `worker`） |
| 队列不消费 | `queue_pending` 非空但无活 Worker 时全局 watchdog（30s tick）会自动拉起；持续不动查 `data/logs/pan.log` 的 watchdog/branch 日志 |
| 端口占用 | 换 `port` 或 `PAN_PORT`；确认旧实例已 `stop_pan.bat` 树杀 |
| MCP 工具搜不到 | `--mcp-config` 路径下工具应直接可见（非 deferred）；搜不到 = 未接线，查 mcp-config 生成与 `cwd` |
| `report_subscribe` 返回 404 / 失效 | **三对齐**没满足：`PAN_API_URL` 端口、`PAN_AGENT_SESSION_ID` 所在服务、mcp-config `cwd` 必须同实例；404 还可能是运行中服务版本落后（无 report-subscribe 路由），此时用轮询兜底（`session_get` 轮 `lastResult.status`） |
| Windows curl 中文报错 | `{"detail":"There was an error parsing the body"}`——改 `--data-binary @body.json`（UTF-8）或 python requests |
| `session_list` 输出过大 | 全量返回含 history（实测可到 300KB+）。巡检用 `summary=true`（HTTP `?summary=1`），详情用 `session_get(limit=15)` |
| QQ 连不上 | 查 NapCat/LLOneBot 是否启动、`ONEBOT_WS_URLS` / `qq.<channel>.ws_urls` 是否指向正确端口（3001/3002）；QQ bot 崩溃看 `data/logs/pan.log` 启动告警（Pan Core 不受影响） |
| 带 character 的会话首个任务卡顿 | embedding 首次加载/网络重试；等 15s 超时降级，或 `memory.enabled: false` |
| Worker 报 "Worker process dead" | 进程已崩溃/被回收，重新 `agent_spawn`（自动恢复上下文） |
| 删除 Session 后 workdir 残留 | 设计如此（delete 不删磁盘目录），需要时手动清理；CLI 原生会话可重新导入复用 |

---

## 13. 安全与运维提示

- **API 无鉴权**，默认绑 `127.0.0.1` 是有意设计。改 `PAN_HOST` 为非 loopback 会把全部端点暴露到网络（启动时打印告警）。`pan_access` 隔离只在 MCP 层生效，HTTP/前端是最高权限。
- **config.json 已 gitignored**：端口、QQ token（`ONEBOT_ACCESS_TOKEN`）、`remote.config_path` 等都在其中，不要提交；凭据也不进代码库。
- **数据落盘位置**（均相对项目根）：`data/sessions/`（Session 元数据 + `.history.jsonl`）、`data/workdirs/`（默认工作目录）、`data/mcp-configs/`（每会话 MCP 配置）、`data/characters/`、`data/memory/`（SQLite 记忆库）、`data/logs/pan.log`、`data/qq_bot.pid`。备份/迁移按目录整体拷贝。
- **Memory 依赖降级**：`minimal-requirements.txt` 不含 ML 链；向量检索需 `sentence-transformers`，缺失时懒加载降级不影响 Core；`jieba` 缺失会降低中文检索质量。
- **Remote 公网暴露**：Cloudflare Tunnel 侧无鉴权，公网可访问全部 API——仅在理解风险时开启，建议叠加 Cloudflare Access 等外部防护。
- **git worktree 场景**：worktree 无独立 `.venv`，统一用主仓库解释器。

---

## 关联文档

- `README.md` — 项目概览、卖点、69 端点 API 索引
- `docs/skills/pan/SKILL.md` — 编排知识单一事实源（Meta-Agent 冷启动手册；MCP 内 `pan_handbook` 返回其全文）
- `docs/skills/pan/references/http-api.md` / `ws-protocol.md` — HTTP/WS 技术细节与轮询兜底策略
- `docs/references/cli-adapter-special-behaviors.md` — 各 CLI 特殊行为
- `importantInfo.md` — 端口与启动顺序速查
