# Pan

> 一个入口，管理所有任务——只跟一个 Meta-Agent 对话，它拆解并调度一整支 CLI Agent 工人团队并行干活。

**[English](./README.en.md) · 中文**

Pan 是一个 **CLI Agent 编排调度平台**（orchestrator）：Supervisor/Worker 架构下，一个「Meta-Agent 主管」通过 MCP 工具与 WebSocket 事件流，同时指挥多个 Worker（每个 Worker 是独立运行的 CLI Agent 会话）并行推进任务，每个 Worker 在独立的 git worktree 中工作。你可以在 Web Dashboard、QQ、公网隧道或任意 Agent CLI 上指挥它，也随时可以旁观、插话或接管某个 Worker 的终端。

- **技术栈**：Python + FastAPI + WebSocket + SQLite（FTS5 全文检索）+ 可选 embedding 向量检索；前端为 React（开发主力）+ Vanilla JS（稳定备份）双轨。

---

## 目录

- [简介](#简介)
- [特性](#特性)
- [核心概念](#核心概念)
- [快速开始](#快速开始)
- [架构](#架构)
- [多 CLI 适配](#多-cli-适配)
- [Meta-Agent 编排](#meta-agent-编排)
- [配置](#配置)
- [API 概览](#api-概览)
- [通道与集成](#通道与集成)
- [运行须知](#运行须知)
- [文档](#文档)
- [贡献](#贡献)
- [许可证](#许可证)

---

## 简介

传统的一对一 AI 编程助手是「你说一句，它干一件」。Pan 把这种模式升级为**一对多**：你只跟一个主管对话，主管同时调度多个 Worker 并行干活，再汇总成一份结果回报给你。

典型使用场景：

- **多任务并行**——同时推进同一项目的多个子任务、多个项目，乃至生活琐事（日程、提醒、自动化）；
- **多 CLI 并存**——不同任务交给不同 CLI Agent，切换 CLI 不丢上下文；
- **AI 有记忆**——向量 + 全文混合检索，开工自动注入相关记忆，人设跨 Session 保持；
- **多渠道指挥**——Dashboard / QQ / 公网隧道 / MCP 任意入口，操作同一个调度台。

## 特性

- **Meta-Agent 编排（SMA）**：一个主管完成「拆解 → 并行派发 → 订阅汇报 → trust-but-verify 验收 → 合并交付」的完整编排闭环。
- **多 CLI 协议化适配**：`CliAdapter` 协议 + 注册表，内置 **cbc / kimi / opencode / claude / codex** 五个 adapter，编排层对底层 CLI 无感知。
- **替身交接（session_handoff）**：切换 CLI 时创建孪生会话接替旧会话，关系网 / 订阅 / 报告随行，只携带精简摘要，避免上下文膨胀。
- **Managed 订阅收件箱**：订阅制报告落盘投递，主管「派完活回来看收件箱」，掉线重连不丢报告。
- **Worker 生命周期自愈**：`stream` / `one-shot` 双执行模式；Watchdog 三档超时清理 + 落盘队列在进程异常死亡后自动重建 Worker。
- **Memory + Character**：SQLite FTS5 + embedding 混合检索；人设（Character）与记忆库跨 Session 保持同一身份。
- **会话级 MCP**：每个 Session 可挂载自己的 MCP Server；内置 `pan`（27 个编排工具）与 `pan-qq`（6 个 QQ 工具）两个 server。
- **多渠道接入**：Web Dashboard（React + Legacy 双轨）、QQ Bridge（NapCat / LLOneBot 通道插件化）、Cloudflare Tunnel、任意 Agent CLI（WS + MCP）。
- **会话导入**：cbc / kimi / opencode / claude / codex 历史会话可导入复用，免去重新探索与初始化。

## 核心概念

| 概念 | 说明 |
|------|------|
| **Session** | 持久化的对话容器（`ses_<16hex>`），独立于 Worker 生命周期 |
| **Worker** | 临时的 CLI Agent 子进程，绑定一个 Session；`stream` 长驻 / `one-shot` 一次性两种形态 |
| **Meta-Agent（SMA）** | 主管角色：不亲自干活，只负责拆解、派活、听汇报、验收 |
| **CLI Adapter** | 每种 CLI Agent 一个协议化适配器（cbc / kimi / opencode / claude / codex） |
| **session_handoff** | 替身交接：创建孪生 Session 接替旧会话，关系网 / 订阅 / 报告随行 |
| **assign** | 异步派发任务（taskId 幂等），完工后收到报告 |
| **report-subscribe** | 订阅制报告：Worker 完工自动把报告投到主管的落盘收件箱 |
| **claim** | 建立主管 ↔ Worker 的双向管理绑定 |
| **branch** | 从现有 Session fork 出独立分支，继承模型 / 记忆 / 工具 |
| **takeover** | 把 AI 会话夺回人类终端亲自接管 |
| **Watchdog** | 每个 Worker 一只：卡死 / 超时自动清理；全局级自动补员 |
| **Memory** | 向量 + 全文（FTS5）混合检索，开工前自动注入相关记忆 |
| **Character** | 人设 + 独立记忆库，跨 Session 保持同一身份 |
| **QQ Bridge** | QQ 消息 ↔ Worker 指令；NapCat / LLOneBot 通道可切换 |
| **Remote** | Cloudflare Tunnel，把调度台暴露到公网 |

## 快速开始

### 前置要求

- Python 3.14（当前开发环境为 3.14.5）
- Node.js + npm（编译 legacy 前端）

### 安装与启动

```bash
# 1. 安装最小依赖（仅核心，不含 Memory 的 ML 链）
pip install -r minimal-requirements.txt

# 2. 生成配置
cp config.example.json config.json
# Windows: copy config.example.json config.json
# 所有字段可选；models 不填时自动识别可用模型

# 3. 编译 legacy 前端（TS 源码 → static/js/app.js）
#    必须在项目根执行（根 tsconfig，而非 packages/web 的 React tsconfig）
npx tsc

# 4. 启动
python main.py
# → http://127.0.0.1:8768
#   main 分支默认 8768；test 分支默认 8767；可用 PAN_PORT 覆盖

# 5. 运行测试
python -m pytest tests/ -q
```

### React 前端（开发中）

```bash
cd packages/web
pnpm install   # 首次
pnpm build     # 产物 → packages/web/dist/
pnpm dev       # 开发模式：Vite HMR + 代理到后端
```

访问路由由 `config.json` 的 `frontend` 字段控制：

| `frontend` | 行为 |
|------------|------|
| `coexist`（默认） | `/` 旧前端 + `/react/` React SPA |
| `react` | React 接管 `/` |
| `legacy` | 仅旧前端 |

> 后端 API/WS 优先为 React 演化；若后端变更破坏 legacy 前端，改 `ts/app.ts` 跟随，不约束后端。

## 架构

```
         Meta-Agent                   人类                    远程访问
    (Agent CLI / MCP)           (Dashboard)            (Cloudflare Tunnel)
          │                          │                          │
   /ws/agent + MCP tools       /ws + HTTP               公网 URL + WS
    （事件流 + 命令）          （观察 + 注入 + 接管）     （Dashboard / QQ Bot 外部接入）
          │                          │                          │
          └──────────┬───────────────┘                          │
                     │                                          │
            ┌────────▼────────┐                                 │
            │  Pan Core         │◄──────────────────────────────┘
            │  (FastAPI 服务)    │        HTTP / WebSocket
            │                   │
            │  Session Manager │
            │  ├─ Worker-1     │── CliAdapter 协议（cbc / kimi / opencode / claude / codex）
            │  ├─ Worker-2     │── ...（互不感知，按 adapter 名路由）
            │  └─ Worker-N     │
            │                   │
            │  Character 框架   │── profile → character → memory
            │  Memory 子系统    │── SQLite + FTS5 + embedding 检索
            │  Event Bus       │─── WS 广播
            │  Session Store   │─── JSON 持久化
            └──────────────────┘
```

### 模块划分

| 目录 | 职责 |
|------|------|
| `packages/core/` | Core 模块：进程管理 + 消息路由 + Memory + Adapter。所有外部模块仅通过 HTTP/WS API 与 Core 通信 |
| `packages/web/` | Web 通道：FastAPI 路由 + WebSocket + Dashboard（69 个 HTTP 端点） |
| `packages/qq/` | QQ 通道：NoneBot2 桥接 + 通道插件化 + pan-qq MCP |
| `packages/mcp/` | MCP Server：27 个工具，可独立启动 |
| `packages/remote/` | Cloudflare Tunnel 远程通道 |
| `scripts/` | 启动 / 停止 / 隧道 / 预提交脚本 |
| `docs/` | 文档（git 跟踪；`docs/skills/pan/SKILL.md` 是编排知识单一事实源） |
| `tests/` | 测试（26 个文件） |

## 多 CLI 适配

Worker 与具体 CLI 解耦：每种 CLI Agent 对应一个实现 `CliAdapter` 协议（`packages/core/adapters/base.py`，元信息 / 进程启动 / 消息编码 / 事件解析 / 接管五组方法）的 adapter，启动时在注册表（`packages/core/adapters/registry.py`）中按名注册。

| Adapter | CLI | 执行模式 | 说明 |
|---------|-----|---------|------|
| `cbc` | CodeBuddy CLI | stream + one-shot | 原生 JSON 流协议，主力 adapter |
| `kimi` | Kimi CLI | stream（wrapper 长驻） | wrapper 内逐条 `kimi -p` |
| `opencode` | OpenCode CLI | stream（wrapper 长驻） | wrapper 内逐条 `opencode run --format json` |
| `claude` | Claude Code CLI | one-shot | 逐条 `claude -p --output-format stream-json`，MCP 经 `--mcp-config` 注入 |
| `codex` | OpenAI Codex CLI | stream（wrapper 长驻） | wrapper 内逐条 `codex exec --json`，MCP 经 `~/.codex/config.toml` 内联注入 |

配套的 `SessionsProvider` 协议（`packages/core/adapters/base.py`）把各 CLI 的原生会话存储（历史 / usage / 标题 / fork）统一为同一套读写接口；server 按 adapter 名取 provider，新增一个 CLI 无需再写 import / branch / rename 的分派逻辑（`/api/adapters/{adapter}/sessions[/import]` 通用端点）。

模型配置遵循「少配」原则：`config.json` 中 `models` 字段**不填 = 自动识别**该 CLI 的可用模型（cbc 解析 `--help`、kimi 解析 config.toml），**填写 = 限制可用模型**。

## Meta-Agent 编排

Meta-Agent 不是某个特殊程序，而是一个**角色**——任何一方（你的 Agent CLI、脚本、甚至另一个 Pan 会话）只要满足三个条件即可扮演「主管」：

1. **能发指令**：通过 MCP 工具（27 个，如 `worker_spawn` / `worker_assign` / `worker_send` / `session_handoff`）或 HTTP API；
2. **能收情报**：通过 WebSocket 订阅事件流（`worker.result` / `worker.status` / `worker.crashed`…），或订阅制报告落盘到自己的收件箱；
3. **有身份**：Pan 记录谁在指挥，并对 Worker 做隔离防止越权。

Pan 内置 **SMA（Super Meta Agent）编排模板**（`manifest.json` 的 `session_templates.SMA`）：一键创建「超级编排代理」会话，挂载 Pan 核心 MCP 与 QQ 通道 MCP，全权限 + 自动认领 + 自动订阅，开箱即用的 AI 项目经理。

### 编排方法论

SMA 的调度遵循一套方法论（固化在 `docs/skills/pan/SKILL.md`）：

1. **决策三问**——先判断拆不拆：① 能真并行吗？② 拆了更快吗？③ 精度关键吗？任一不过 → 自己做；全过 → 并行派发；
2. **并行派发**：`worker_assign` 异步分发到多个 Worker（各自独立 git worktree，避免提交冲突），立即返回不阻塞；
3. **订阅制汇报**：`report_subscribe` 把完成报告自动投进主管的落盘收件箱，掉线重连报告不丢；
4. **trust-but-verify 验收**：合并汇报前逐项核对改动、跑测试验证；
5. **合并汇报**：收回全部结果，汇总成一份交付。

### 编排层对底层 CLI 无感知

SMA 只通过 MCP 工具 / WS 事件流与 Worker 通信，不知道也不关心 Worker 底下跑的是哪个 CLI。因此「什么任务派给哪个 CLI」是**可配置**的：通过写 SMA 的模型规则（system prompt），即可按任务类型路由——例如「重活走 cbc、轻量调研走 kimi、写作走 opencode」，集群本身无需任何改动。

## 配置

配置文件为仓库根目录的 `config.json`（gitignored），模板见 `config.example.json`。所有字段可选，省略时使用 `packages/core/config.py` 内置默认值。

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `port` | 8768 | 主服务端口（main 分支）；test 分支 8767 |
| `frontend` | `coexist` | `coexist` / `react` / `legacy` |
| `cbc.model` | `deepseek-v4-flash` | cbc 默认模型 |
| `cbc.models` | `[]` | 不填 = 自动识别（cbc `--help` 解析）；填写 = 限制可用模型 |
| `cbc.permission_mode` | `bypassPermissions` | cbc 权限模式 |
| `kimi.model` | `moonshot-cn/kimi-k2.6` | kimi 默认模型 |
| `kimi.models` | `[]` | 不填 = 自动识别（config.toml 解析）；填写 = 限制可用模型 |
| `worker.timeout_sec` | 300 | queued 静默超时 / 运行中无输出读取超时 kill 秒数 |
| `worker.task_timeout_sec` | 1800 | stream running 任务运行时长上限（长思考 / 大文件读取不误杀） |
| `worker.idle_sec` | 300 | 空闲回收秒数（held / zombie 跳过） |
| `qq.enabled` | true | 是否启动 QQ bot（main.py 按此统一 spawn / 终止） |
| `qq.mode` | `mirror` | `mirror` 全量镜像自动回复 / `selective` 选择性发送（消息只进 inbox，由 meta-agent 经 pan-qq MCP 决策） |
| `qq.channel` | `napcat` | QQ 通道：`napcat` / `llonebot`（OneBot 11 网关插件化切换） |
| `remote.enabled` | false | 是否启用 Cloudflare Tunnel |
| `remote.quick_tunnel` | true | true 用临时 URL；false 用 named tunnel（需 `remote.config_path`） |
| `remote.status_port` | 8769 | Remote 状态服务端口 |
| `logging` | INFO / `data/logs/pan.log` | 日志级别、轮转、控制台输出 |
| `plugin_manifests` | `["manifest.json"]` | 外部 Character profiles 清单 |

**环境变量**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PAN_PORT` | — | 覆盖 `port` |
| `PAN_HOST` | `127.0.0.1` | 监听地址 |
| `PAN_URL` | `http://127.0.0.1:{port}` | QQ Bridge 访问 Pan Core 的地址 |
| `PAN_API_URL` | `http://127.0.0.1:8768` | MCP server 连接 Pan Core 的地址 |
| `PAN_QQ_API_URL` | `http://127.0.0.1:8080` | pan-qq MCP 连接 QQ bot 的地址 |
| `PAN_QQ_PYTHON` | miniforge | QQ bot 解释器路径 |
| `PAN_QQ_MODE` | — | 覆盖 `qq.mode` |
| `ONEBOT_WS_URLS` / `ONEBOT_ACCESS_TOKEN` | — | 覆盖 QQ 通道连接地址 / token |

## API 概览

### HTTP（`packages/web/server.py`，69 个端点）

**Session 管理**

```
GET    /api/sessions                    → 列举所有 Session
POST   /api/sessions                    → 创建 Session
GET    /api/sessions/{id}               → 获取 Session 详情
GET    /api/sessions/{id}/history       → 获取历史消息（分页）
PATCH  /api/sessions/{id}               → 更新 Session（含 requireRestart 语义）
POST   /api/sessions/{id}/rename        → 重命名
POST   /api/sessions/{id}/branch        → 分支 Session
POST   /api/sessions/{id}/handoff       → 替身交接（创建孪生 Session 接替）
DELETE /api/sessions/{id}               → 删除 Session
POST   /api/sessions/batch-delete       → 批量删除
```

**Worker 管理**

```
POST   /api/spawn                       → 启动新 Worker
POST   /api/task                        → 向 Worker 发送任务
POST   /api/kill/{worker_id}            → 停止 Worker
GET    /api/list                         → 列举活跃 Worker
POST   /api/worker/{id}/restart         → 重启 Worker
POST   /api/worker/{id}/settings        → 更新 Worker 配置
POST   /api/worker/{id}/rename          → 重命名 Worker
POST   /api/worker/{id}/branch          → Worker 分支
POST   /api/worker/{id}/interrupt       → 中断 Worker（仅 running 时）
POST   /api/worker/{id}/takeover        → 接管 Worker 终端（重启 + 置 held）
GET    /api/worker/{id}/takeover-command → 生成接管命令（不执行）
```

**编排**

```
POST   /api/assign                      → 异步派发任务（taskId 幂等）
POST   /api/report-subscribe            → 订阅 Worker 报告（同时建立 managed 关系）
POST   /api/report-unsubscribe          → 退订报告
POST   /api/claim                       → 绑定 managed 关系
POST   /api/unclaim                     → 解除 managed 关系（同时退订报告）
```

**QQ 绑定**

```
POST   /api/qq/subscribe                → Pan session 订阅某 QQ 会话 inbox 更新提醒
POST   /api/qq/unsubscribe              → 取消订阅
POST   /api/qq/notify                   → QQ 插件上报 inbox 更新
GET    /api/qq/contacts                 → 最近 QQ 联系人 / 群
```

**Character / Memory**

```
GET    /api/characters/profiles         → 列出可用 Profile（session templates）
GET    /api/manifest/command-routes     → 列出 QQ 命令路由
GET    /api/characters                  → 列出 Character
POST   /api/characters                  → 创建 Character
GET    /api/characters/{id}             → 获取 Character 详情
DELETE /api/characters/{id}             → 删除 Character
POST   /api/memory/index                → 索引记忆目录（.md → SQLite）
GET    /api/memory/search               → 混合检索记忆
GET    /api/memory/stats                → 记忆库统计
POST   /api/memory/inject               → 手动注入记忆
```

**文件系统（session workdir 内，含路径逃逸校验）**

```
GET    /api/fs/list                     → 列出目录
GET    /api/fs/read                     → 读取文件
POST   /api/fs/write                    → 写入文件
POST   /api/fs/rename                   → 重命名
POST   /api/fs/delete                   → 删除
```

**Adapter / 导入**

```
GET    /api/models?adapter=cbc          → 获取模型列表
GET    /api/adapter/config?adapter=cbc  → Adapter 配置
GET    /api/adapters                    → 列举可用 Adapter
GET    /api/adapters/{adapter}/sessions[/import] → 通用会话导入 / 浏览
GET    /api/cbc/projects                → CBC 项目列表
GET    /api/cbc/sessions                → CBC Session 列表
GET    /api/cbc/browse                  → 浏览 CBC Session 文件
POST   /api/cbc/sessions/import         → 导入 CBC Session
GET    /api/kimi/workspaces             → Kimi Workspace 列表
GET    /api/kimi/sessions               → Kimi Session 列表
POST   /api/kimi/sessions/import        → 导入 Kimi Session
```

### WebSocket

```
WS   /ws           Dashboard：仅接收 user_inject；广播全部事件
WS   /ws/agent     Meta-Agent：subscribe（按 eventTypes / sessionIds 过滤 + 重连补发）、
                   reconnect、task、spawn、assign、send、kill、list
```

广播事件：`worker.stream` / `worker.result` / `worker.status` / `worker.spawned` / `worker.crashed` / `worker.zombie` / `worker.destroyed` / `worker.restarted` / `worker.reconfigured`、`session.created` / `session.updated` / `session.renamed` / `session.deleted` / `sessions.deleted`、`error`。

### MCP Server（`packages/mcp/server.py`，27 个工具）

```
session_create / session_import / session_list / session_managed / session_get /
session_delete / session_batch_delete / session_handoff / session_claim /
session_claim_many / session_unclaim / session_unclaim_many / session_update /
session_history / session_qq_subscribe / session_qq_unsubscribe /
report_subscribe / report_unsubscribe /
worker_spawn / worker_task / worker_kill / worker_list / worker_assign /
worker_send / worker_send_force / model_list / pan_handbook
```

另有独立 **pan-qq MCP server**（`packages/qq/mcp.py`，6 个工具）：`qq_send_message` / `qq_read_conversation` / `qq_list_contacts` / `qq_read_inbox` / `qq_bind` / `qq_unbind`。

启动方式：`python -m packages.mcp.server --transport stdio|sse|streamable-http [--port 9740]`（默认 stdio，API 地址取 `PAN_API_URL`）。

## 通道与集成

### Web / Dashboard

- `http://127.0.0.1:{port}` — legacy Dashboard；`/react/` — React Dashboard
- `ws://127.0.0.1:{port}/ws` — Dashboard WebSocket
- `ws://127.0.0.1:{port}/ws/agent` — Meta-Agent WebSocket

### Meta-Agent（MCP）

Pan 内置 `pan` MCP server（27 个工具），任意 Agent CLI 通过 MCP 协议（stdio / SSE / streamable-http）接入即可扮演 Meta-Agent，也可以直接连 `/ws/agent` WebSocket 订阅事件流并下发命令。启动方式见「[MCP Server](#mcp-serverpackagesmcpserverpy27-个工具)」。

### QQ Bridge

依赖见 `packages/qq/requirements.txt`（nonebot2 + onebot-adapter-onebot + httpx）。启动：

1. 启动所选网关：NapCat（正向 WS 服务端，端口 3001）或 LLOneBot（端口 3002），由 `config.json` 的 `qq.channel` 指定；
2. `python main.py`（或 `scripts/start_pan.bat`）——main.py 按 `config.json` 的 `qq.enabled` 自动 spawn / 终止 QQ bot（`packages/qq/bot.py`，PID 写入 `data/qq_bot.pid`），无需手动启动。

> 注意：QQ bot 运行在 miniforge 解释器（NoneBot 未装在项目 .venv），可用 `PAN_QQ_PYTHON` 覆盖。

QQ 接入被抽象为可切换的**通道（Channel）**：`QQChannel` 接口（`packages/qq/channels/base.py`）定义生命周期 / 消息回调 / 收发 / 联系人查询；NapCat 与 LLOneBot 都是 OneBot 11 网关的薄子类（`packages/qq/channels/`），业务层只依赖接口，切换网关零改动。

`qq.mode` 控制桥接行为：`mirror`（全量镜像自动回复，默认）/ `selective`（消息只进 inbox + history，由 meta-agent 经 pan-qq MCP 决策回复）。`manifest.json` 的 `command_routes` 可声明 QQ 前缀命令直发外部 HTTP API（不走 LLM）。

### Remote（Cloudflare Tunnel）

```bash
python -m packages.remote
# 或 scripts/start_cf.ps1
```

- `quick_tunnel: true` → 输出 `*.trycloudflare.com` 临时 URL；`false` → 需 `remote.config_path` 指定 named tunnel 的 yml
- 状态服务：`curl http://127.0.0.1:8769/status`
- 公网域名来自 `config_path` 指向的 yml 的 `ingress.hostname`；tunnel 暴露的是 Pan 主端口（`config.port`）

## 运行须知

- **安全模型**：API 无鉴权，默认绑定 `127.0.0.1`（loopback）是有意为之。把 `PAN_HOST` 改成非 loopback 会把所有端点暴露到网络（`main.py` 启动时会告警）。安全重点在边界校验：workdir 路径逃逸校验、character_id 格式校验。
- **端口速查**：Pan 主服务 8768（main）/ 8767（test）；Remote 状态 8769；NoneBot2 8080（不对外）；NapCat 3001 / LLOneBot 3002。
- **Worker 超时语义**：stream running 按**任务运行时长**判定卡死（`worker.task_timeout_sec`，默认 1800s）；queued 用静默超时（`worker.timeout_sec`，默认 300s）——长思考 / 大文件读取不会被误杀。
- **Worker 双模式**：`stream` 长驻（可挂载 MCP）；`one-shot` 一次性（仅 `output_mode=oneshot` 时启用）。派发统一走 `worker_assign` / `worker_send`（阻塞式 `worker_handoff` 已于 2026-08-26 移除，串行依赖同样走 assign + report_subscribe）。
- **Memory 依赖与降级**：`minimal-requirements.txt` 不含 ML 链；启用向量检索需 `sentence-transformers`（web 端默认 embedding provider）。可选库缺失时懒加载 + ImportError 兜底自动降级，不影响 Core 启动；`jieba` 缺失会显著降低中文检索质量。
- **QQ bot 进程管理**：main.py 按 `qq.enabled` 统一 spawn / 终止（写 `data/qq_bot.pid`）；`scripts/stop_pan.bat` 精确树杀，不全局杀 python.exe。
- **worktree 无独立 .venv**：在 git worktree 里测试 / 运行时，统一使用主仓库的 `.venv`。
- **Python 版本**：仓库无版本声明文件（无 pyproject.toml / .python-version），实际运行环境为 Python 3.14.5。

## 文档

- [`docs/skills/pan/SKILL.md`](docs/skills/pan/SKILL.md) — Pan 编排知识单一事实源（冷启动手册、MCP 工具约定、坑与约定）
- [`docs/design/`](docs/design/) — 设计文档（adapter 架构、kimi / opencode 适配、one-shot 模式等）
- [`docs/plans&overviews/`](docs/plans&overviews/) — 立项规划与实现记录
- [`docs/references/`](docs/references/) — 参考笔记
- [`importantInfo.md`](importantInfo.md) — 端口与启动顺序速查

## 贡献

- 开发采用 **git worktree 并行分支**模式：每个功能在独立 worktree / 分支上开发，合入 main 前先过测试。
- **前端双源约定**（详见 `CODEBUDDY.md`）：
  - legacy 源码在 `packages/web/ts/app.ts`，`static/js/app.js` 是编译产物（gitignored），**禁止直接改产物**；改完从项目根执行 `npx tsc`；
  - React 源码在 `packages/web/src/`，产物 `dist/`（gitignored）；改完执行 `cd packages/web && pnpm build`；
  - pre-commit（`git config core.hooksPath scripts`）会同时校验 legacy（`tsc --noEmit`）与 React（`pnpm build`）。
- 运行测试：`python -m pytest tests/ -q`。
- 若改动 MCP 工具 / HTTP API / workdir 约定，请同步更新 `docs/skills/pan/SKILL.md`（单一事实源）。

## 许可证

本仓库目前未附带开源许可证文件（LICENSE）。如需使用 / 分发 / 修改，请先与作者确认许可条款。
