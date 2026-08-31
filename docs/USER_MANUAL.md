# Pan 用户手册

> 面向「想真正把 Pan 用起来」的使用者的完整操作指南。概念速览见 README；编排冷启动手册（Meta-Agent 视角）见 `docs/skills/pan/SKILL.md`；本手册是两者之上的全功能实操参考。
>
> 适用版本：main 分支（commit aa430a0 之后）。文中端口默认 main 分支 8768（test 分支为 8767）。

**[English](./USER_MANUAL.en.md) · 中文**

## 目录

1. [什么是 Pan](#1-什么是-pan)
2. [安装与启动](#2-安装与启动)
3. [快速上手（界面操作）](#3-快速上手界面操作)
4. [UI 使用指南](#4-ui-使用指南)
5. [配置方法](#5-配置方法)
6. [最佳实践](#6-最佳实践)
7. [核心操作详解](#7-核心操作详解)
8. [编排实践：Meta-Agent 指南](#8-编排实践meta-agent-指南)
9. [通道：Web / QQ / Remote](#9-通道web--qq--remote)
10. [故障排查](#10-故障排查)
11. [安全与运维提示](#11-安全与运维提示)
12. [开发者与 API 参考](#12-开发者与-api-参考)
13. [关联文档](#13-关联文档)

---

## 1. 什么是 Pan

Pan 是一个 **CLI Agent 编排调度平台**（orchestrator）：Supervisor/Worker 架构下，一个「Meta-Agent 主管」（又称 SMA，Super Meta Agent）通过 MCP（Model Context Protocol）工具与 WebSocket（WS）事件流，同时指挥多个 Worker 并行干活。传统 AI 编程助手是一对一对话；Pan 是**一对多**——你只跟一个主管对话，它拆解任务并调度一整支 CLI Agent 工人团队。

### 1.1 定位光谱

从最小用法到最全用法：

| 层级 | 用法 | 涉及组件 |
|------|------|----------|
| 最小 | 一个入口管理多个 CLI 会话：建会话、派任务、看结果 | **Web Dashboard（推荐）** / HTTP API |
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
- Node.js + pnpm（构建 React 前端）
- 至少一个受支持的 CLI：`cbc`（CodeBuddy CLI）、`kimi`、`opencode`、`claude`、`codex`

Pan 不负责安装第三方 Agent CLI。请在**启动 Pan 的同一个终端 / 用户环境**中，任选至少一个 CLI 验证全局安装：

```bash
cbc --version
kimi --version
opencode --version
claude --version
codex --version
```

至少一条命令应输出版本号。Pan 启动时会在日志中逐个报告 CLI 的 `ready/unavailable` 状态；缺少某个 CLI 不会阻止 Pan 启动，但使用对应 adapter 创建 Worker 时会给出安装、PATH 和重启提示。如果所有 CLI 都缺失，Worker 无法运行。后台服务的 PATH 可能和交互式终端不同，安装 CLI 或修改 PATH 后请重启 Pan。运行中的诊断可通过 `GET http://127.0.0.1:8768/api/cli/status` 查看。

> 前端说明：**React Dashboard 是当前唯一维护并推荐的前端**。旧版 Vanilla 前端已弃用（deprecated），仅在 `/vanilla` 路由作后备访问（见 §12.3），不建议新用户使用。

### 2.2 安装步骤

```bash
# 1. 安装最小依赖（仅核心，不含 Memory 的 ML 链）
pip install -r minimal-requirements.txt

# 2. 生成配置（所有字段可选，省略用默认值）
cp config.example.json config.json        # Windows: copy config.example.json config.json

# 3. 构建 React 前端（推荐；产物 → packages/web/dist/）
cd packages/web && pnpm install && pnpm build && cd ../..

# 4. 启动
python main.py
# → http://127.0.0.1:8768
```

`scripts/` 下另有免手动步骤的脚本：`setup.bat` / `setup.sh`（安装依赖、生成 config.json、探测 QQ 解释器等）、`start_pan.bat` / `start.sh`（启动）、`stop_pan.bat` / `stop.sh`（停止；Windows 版按 PID 文件做精确进程树杀，不误伤其他 python 进程）。

**macOS / Linux 快速路径**（脚本自动完成上面的步骤 1-3）：

```bash
bash scripts/setup.sh    # 首次：建 .venv + 装依赖 + 生成 config.json + 构建前端
bash scripts/start.sh    # 后台启动（PID 记 data/process.pid，日志 data/pan.out.log）
bash scripts/stop.sh     # 停止
```

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
| `PAN_PYTHON` | 当前 Pan 解释器 | manifest 中 `pan` / `pan-qq` stdio MCP server 使用的 Python 解释器；git worktree 可用它指向共享主仓库 `.venv` |
| `PAN_URL` | `http://127.0.0.1:{port}` | QQ Bridge 访问 Pan Core 的地址 |
| `PAN_QQ_API_URL` | `http://127.0.0.1:8080` | pan-qq MCP 连接 QQ 插件的地址 |
| `PAN_QQ_PYTHON` | 平台默认 | QQ bot 解释器路径 |
| `PAN_QQ_MODE` | — | 覆盖 `qq.mode` |
| `ONEBOT_WS_URLS` / `ONEBOT_ACCESS_TOKEN` | — | QQ 通道 WS 地址 / token（可写在 `packages/qq/.env`） |

### 2.4 停止

Ctrl+C 优雅退出（QQ bot 子进程随之终止）；或用 `scripts/stop_pan.bat` / `stop.sh`。

### 2.5 macOS / Linux 专属说明

- **路径大小写敏感**：macOS/Linux 文件系统大小写敏感——`.venv`、`config.json`、`packages/qq/.env` 等路径务必与文档一致，大小写写错会导致找不到文件；
- **QQ 解释器**：解释器路径由 `main.py` 自动解析（`PAN_QQ_PYTHON` 环境变量 > `config.json` 的 `qq.python` > 平台默认）；macOS/Linux 上默认即 `python3`，无需手动配置——仅当要用特定解释器时才设置 `qq.python` 或 `PAN_QQ_PYTHON`；不用 QQ 就在配置里设 `qq.enabled=false`，可跳过 setup.sh 的 QQ 依赖步骤；
- **进程组收割**：Linux 有 `setsid` 时，`start.sh` 让 main.py 成为独立进程组组长，`stop.sh` 整组收割；macOS 默认无 `setsid`，退化为普通后台进程，`stop.sh` 递归 TERM 子进程（含 QQ bridge）后优雅关停——**只杀记录在案的 PID + 进程组，绝不误伤其它 python 进程**。

---

## 3. 快速上手（界面操作）

这一章带你用**浏览器界面**走完第一个任务：从启动服务到看到 AI 输出结果，全程不需要命令行。

### 3.1 启动服务并打开界面

> **实在不想读文档？** 启动服务后，直接新建一个 `SMA(NoAdapter)` 会话，问它一句「怎么玩转 Pan？」——它会调出编排手册（`pan_handbook`）现场教你一步步来。

```bash
# Windows
python main.py

# macOS / Linux（后台启动；首次先装依赖）
bash scripts/setup.sh    # 仅首次
bash scripts/start.sh
```

浏览器打开 <http://127.0.0.1:8768>（默认端口；改端口见 §5.2）。你会看到 Pan 的 **React Dashboard**：左侧是会话列表（Sidebar），右侧是聊天主区。

### 3.2 新建一个会话

1. 在左侧栏顶部点击 **New** 快速创建一个默认命名的新会话；或点击旁边的 ⚙ 齿轮，打开**新建会话配置弹窗**，设置：
   - **Adapter（CLI）**：选择用哪个 CLI Agent 干活（cbc / kimi / opencode 等，见 §7.9）；
   - **会话名称**：起一个能代表任务的名字（例如 `fix-login-bug`）；
   - **工作目录（workdir）**：AI 干活时能读写哪些文件（默认 `data/workdirs/<会话名>`）；
   - **会话模板**：可选，例如 SMA 模板会预置 Meta-Agent 编排能力（见 §8）。
2. 创建后，会话出现在左侧列表中，卡片上带状态点、adapter 徽标、模型、消息数等。

> 模型、权限模式、思考档位等更多设置不必在新建时决定——随时可以在会话设置里改（见 §4.6）。

### 3.3 启动 Worker 并发送任务

1. 选中刚创建的会话，点击顶栏的 **Start** 启动 Worker（拉起对应的 CLI Agent 进程）。
2. 在底部输入框输入任务，例如：`修复 src/utils.py 里的空指针问题，并跑通单元测试。`
3. 按 **Enter** 发送（Shift+Enter 换行）。Worker 忙碌时，消息会自动进入**发送队列**，空闲后按序处理。

### 3.4 观察实时输出

- 顶栏状态点随 Worker 状态变色：**绿色 = 空闲，蓝色 = 运行中，黄色 = 已被你接管，红色 = 出错**；
- 聊天流逐条显示 Worker 的回复；AI 调用的工具（tool）折叠成一行，点击可在右侧 **DetailPanel** 看原始输出；思考过程可内联展开；
- 当前 React 前端保留 **TUI**（终端风格）视图：用户输入带绿色边框，消息块带左侧色条；完成消息沿用 `--- [DONE] Task completed` 风格。此前代码中的 Bubble/TUI 命名曾反过来，现已按实际语义修正。原 **Bubble**（气泡）视图及顶栏切换按钮目前已隐藏并标记为 deprecated，后续如需启用可恢复。

### 3.5 查看结果与后续

- 任务完成，状态点回到绿色（idle），回复停在聊天流里；
- 随时可继续在输入框追问（多轮对话）；
- 让 AI 改完文件后，切到 **Editor** 视图浏览/编辑会话工作目录里的文件（见 §4.4）；
- 想清理，右键会话 → Delete（见 §4.5）。

> 完整功能地图见第 4 章「UI 使用指南」；「什么时候该怎么做」的建议见第 6 章「最佳实践」。

---

## 4. UI 使用指南

本章以 React Dashboard（`packages/web/src/`）的实际界面为基准，介绍每个区域的用途与操作方式。

### 4.1 整体布局

打开首页后，界面分两大块：

- **左侧 Sidebar**：会话列表，顶部是导航与新建入口；
- **主区**：三个视图，由侧栏或快捷键切换：

| 视图 | 路由 | 用途 |
|------|------|------|
| 聊天（Chat） | `/` | 与单个会话对话：发任务、看输出、调设置 |
| 编辑（Editor） | `/editor` | 浏览/编辑会话工作目录里的文件 |
| 管理（Manage） | `/manage/:id` | 会话管理面板：认领/被认领、MCP 权限等 |

快捷键：**Ctrl+B** 折叠/展开侧栏（折叠后变窄图标栏）；**Ctrl+1 / Ctrl+2** 在聊天/编辑视图间切换；**Ctrl/Cmd+K** 呼出命令面板（§4.7）。

### 4.2 会话列表与新建

- **New** 按钮：快速创建默认命名的新会话；
- **⚙ 新建配置弹窗**（NewSessionModal）：新建时可设置 Adapter、会话名、工作目录、会话模板；Output Mode 仅对支持多模式的 adapter（如 cbc）出现；
- 会话卡片（SessionItem）显示：状态点、adapter 徽标、消息数、模型、工作目录、credit 等；
- 列表支持搜索过滤、排序，并按目录或 manager 分组（侧栏上方的控件）；
- 每个会话右上角/选中后可看到**顶栏**（TopBar）：Start（启动 Worker）、Restart、Interrupt（中断当前任务）、Takeover（本地接管终端）、Kill，以及状态点与 worker ID。

### 4.3 聊天视图

- **发消息**：底部输入框（InputRow），Enter 发送、Shift+Enter 换行；输入框左侧齿轮打开会话设置（§4.6）。
- **发送队列**（SendQueuePanel）：Worker 忙碌时消息自动入队；队列面板里可编辑、排序、删除、合并发送，或一键清空；Agent 队列单独分组。
- **消息流**（ChatMessages）：显示历史与实时回复；tool 调用折叠为可点击行，点击后右侧 **DetailPanel** 显示原始输出；thinking 块可内联展开。
- **展示风格**：当前固定使用 TUI 风格；Bubble 风格实现暂保留但已 deprecated，切换入口暂时隐藏。两种风格的历史代码命名已反转修正。

### 4.4 编辑视图

- 左侧**文件树**（FileTree）：浏览会话工作目录，可重命名/删除文件；
- 主区**多标签编辑器**（EditorPane）：同时打开多个文件；Markdown 文件支持 **Edit / Preview / Split** 三种模式（编辑/预览/分屏）。

### 4.5 会话管理操作

在会话上右键（或选中后的菜单，SessionMenu）：

| 操作 | 说明 |
|------|------|
| Rename | 重命名（同步回写 CLI 原生存储） |
| Reimport | 重新导入底层 CLI 会话 |
| Branch | 分支：从当前会话 fork 一个独立副本（继承设置，互不影响；仅 CLI 会话可用） |
| Manage | 打开管理面板（ManageModal） |
| Postbox | 打开收件箱订阅（PostboxModal） |
| Select | 进入多选模式，批量删除 |
| Delete | 删除会话（同时 kill Worker；不删 workdir 磁盘目录） |

- **管理面板**（ManageModal）三节：① 被谁管理（可解绑）；② 管理谁（claim/unclaim + 订阅完成报告）；③ MCP 权限与 MCP 服务器选择。
- **导入会话**（ImportModal）：从 cbc / kimi / opencode / claude / codex 浏览并导入既有 CLI 历史会话，复用历史上下文；Codex 可按工作目录筛选，留空显示全部原生 thread。
- **收件箱订阅**（PostboxModal）：把某个 QQ 会话订阅到当前会话的收件箱，QQ 新消息会以提醒形式推入 `queue_pending`（配合 QQ 通道使用，见 §9.2）。

### 4.6 设置

- **会话设置**（SettingsPopover，输入框左侧齿轮）：模型、权限模式、Thinking/Effort 思考档位、Output Mode，以及对 Worker 的操作。
- **全局设置**（AppSettingsModal，侧栏齿轮）：会话列表默认分组、消息可见性（meta-agent / task-agent / QQ 消息开关）、主题切换，以及**配置热重载**（适配器 / worker / plugin / memory 配置无需重启即可生效）。

### 4.7 命令面板

**Ctrl/Cmd+K** 呼出 CommandPalette：输入关键字即可快速执行——切换视图、新建会话、跳转会话、切换主题、折叠侧栏等。

---

## 5. 配置方法

配置文件是仓库根目录的 `config.json`（**gitignored**），模板见 `config.example.json`。所有字段可选，省略时使用默认值。下面按「你想达到什么效果」组织常用配置；完整字段速查见 §5.7。

### 5.1 从模板生成配置

```bash
cp config.example.json config.json    # Windows: copy config.example.json config.json
```

编辑 `config.json` 后重启 `python main.py` 生效；适配器 / worker / plugin / memory 部分配置支持热重载（UI 全局设置里有按钮，或 `POST /api/config/reload`）。

### 5.2 改端口

```json
{ "port": 8768 }
```

默认 main 分支 8768、test 分支 8767。也可用环境变量 `PAN_PORT` 覆盖（优先级更高）。改完访问地址跟着变。

### 5.3 默认模型与可用模型

```json
{
  "cbc":  { "model": "deepseek-v4-flash", "models": [] },
  "kimi": { "model": "moonshot-cn/kimi-k2.6", "models": [] }
}
```

- `model`：该 adapter 创建会话时的**默认模型**；
- `models`：**不填（`[]`）= 自动识别**该 CLI 的全部可用模型；**填写 = 限制** UI 里可选模型（例如只想用某个模型的团队可以锁死）。

### 5.4 权限模式（重要，涉及安全）

```json
{ "cbc": { "permission_mode": "bypassPermissions" } }
```

可选：`""`（默认）` / default / acceptEdits / bypassPermissions / plan / dontAsk / auto`。

- `bypassPermissions`（默认）：CLI Agent 执行命令/改文件**无需逐条审批**——自动化编排的设计使然，请在可信环境使用；
- 更保守的 `default` / `acceptEdits`：AI 修改文件前会要求确认，更安全但更打断。

> 无鉴权 + 默认绑 127.0.0.1 是既定设计（§11）。权限模式管的是「AI 干活时的动作」，不是「谁能访问服务」。

### 5.5 前端模式

```json
{ "frontend": "coexist" }
```

| 值 | 效果 |
|----|------|
| `coexist`（默认） | `/` 307 跳转 `/react/`（React Dashboard）；旧前端在 `/vanilla` |
| `react` | 仅 React 接管 `/`（无旧前端入口） |
| `legacy` | 仅旧前端（**已弃用**，不建议） |

### 5.6 Worker 超时（卡死回收）

```json
{
  "worker": {
    "timeout_sec": 300,
    "idle_sec": 300
  }
}
```

- `timeout_sec`：**静默超时**——任务运行中持续无输出超过该秒数视为卡死，自动 kill（默认 300）。长思考/大文件读取不会被误杀（有输出就续命）；
- `task_timeout_sec`：**stream 任务运行时长上限**（默认 1800）——单次任务整体运行超过该秒数会被回收，用于兜底「有输出但死循环」的情况；
- `idle_sec`：**空闲回收**——任务完成后进程闲置超过该秒数被回收，释放资源（默认 300；已被你接管或出错的跳过）。

### 5.7 字段速查表

| 字段 | 默认 | 说明 |
|------|------|------|
| `port` | `8768` | 主服务端口 |
| `frontend` | `"coexist"` | 前端模式：`coexist` / `react` / `legacy` |
| `cbc.model` | `"deepseek-v4-flash"` | cbc 默认模型 |
| `cbc.permission_mode` | `"bypassPermissions"` | 权限模式（见 §5.4） |
| `cbc.always_thinking_enabled` | `false` | 思考开关；false 时 `effort` 不生效 |
| `cbc.effort` | `""` | 思考档位（`none/off/auto/low/medium/high/xhigh/max/ultracode`） |
| `cbc.models` | `[]` | 不填自动识别；填写 = 限制可用模型 |
| `kimi.model` | `"moonshot-cn/kimi-k2.6"` | kimi 默认模型 |
| `cbc_import.*` | 见模板 | 外部会话导入过滤（消息数/时间窗/目录匹配等） |
| `worker.timeout_sec` | `300` | 静默超时（无输出即 kill） |
| `worker.task_timeout_sec` | `1800` | stream 任务运行时长上限 |
| `worker.idle_sec` | `300` | 空闲回收（`held`/`zombie` 跳过） |
| `memory.enabled` | `true` | 记忆注入开关 |
| `qq.enabled` | `true` | 是否启动 QQ bot |
| `qq.mode` | `"mirror"` | `mirror` 自动回复 / `selective` 只进收件箱由编排者处理 |
| `qq.channel` | `"napcat"` | `napcat` / `llonebot` 网关 |
| `qq.python` | `""` | QQ bot 独立解释器路径 |
| `remote.*` | 见模板 | Cloudflare Tunnel：`enabled`/`quick_tunnel`/`config_path`/`status_port` 等 |
| `logging.*` | INFO / `data/logs/pan.log` | 日志级别、文件、轮转 |
| `plugin_manifests` | `["manifest.json", "packages/mcp/manifest.json"]` | 根项目模板 + Pan MCP 清单；外部/private manifest 可在本地 config.json 追加 |

### 5.7.1 Git worktree 与 pan-test 配置对齐

`config.json` 是 gitignored 的本地文件，不能依赖它随分支同步。干净 worktree 没有 `config.json` 时，内置默认值会加载 `manifest.json` 和 `packages/mcp/manifest.json`，分别提供项目模板/角色与 `pan`/`pan-qq` MCP；若还需要 main 上的 private manifest，应在本地配置中追加，不能把机器专属路径提交进仓库。

在 `pan-test` 使用主仓库共享解释器时，先按项目约定把测试分支 fast-forward 对齐到 main，再在 `pan-test` 的本地 `config.json` 中保留与 main 相同的 manifest 语义：

```json
{
  "plugin_manifests": [
    "manifest.json",
    "packages/mcp/manifest.json",
    "<private-manifest 的本机路径>"
  ]
}
```

启动测试实例时设置 `PAN_PYTHON` 为共享解释器并运行当前 worktree 的代码，例如 Windows PowerShell：

```powershell
$env:PAN_PYTHON = '<main-worktree>\\.venv\\Scripts\\python.exe'
& $env:PAN_PYTHON main.py
```

仓库内 manifest 使用 `${PAN_PYTHON}`，未设置时回退到运行 Pan 的解释器；因此不需要提交某台机器的绝对路径。兼容已有外部 manifest 时仍可使用 `${PLUGIN_DIR}/../../.venv`，该占位符会按 manifest 所在目录解析；要让它指向共享主仓库 `.venv`，应在本地 `plugin_manifests` 引用主仓库的 `packages/mcp/manifest.json`，不要修改提交的 manifest 写死路径。启动后先检查 MCP catalog 中存在 `pan`，再验证 Claude session 的 `mcpServers` 和 `--mcp-config` 注入。

---

## 6. 最佳实践

### 6.1 核心范式：SMA 是你的第一入口

Pan 的日常使用推荐遵循「**一个入口**」范式：**你只跟一个 SMA（Super Meta Agent）会话对话**，其余的一切——拆解任务、建会话、派活、回收报告、验收汇总、清理——都交给 SMA 去管。

- **推荐模板：`SMA(NoAdapter)`**——不显式绑定 CLI adapter，是**纯编排者**：它自己不干活，只通过 pan MCP 工具调度其它会话；`SMA` 模板则显式绑定 cbc，需要它顺带亲自干活时用。
- 两个 SMA 模板都预置了完整编排权限（pan_access）：`auto_claim_created`（新建会话自动归管）、`can_claim_unmanaged`（可认领任何无人管理的会话）、`restrict_to_managed=false`（不受归属隔离限制），并挂载 `pan` + `pan-qq` 两套 MCP 工具。
- **意义**：你不需要自己在 Dashboard 手工建一堆会话、手动管每个 Worker 的生命周期与报告——**SMA 帮你管 Pan**。你只管「提目标 → 验收结果」。

> 第 3 章展示的直接操作路径（自己建会话、直接发消息）适合简单/临时任务；需要拆解、并行、汇总的工作请走本范式。
>
> **实在不想看文档？直接问 SMA「怎么玩转 Pan！」**——SMA 会用 `pan_handbook` 调出编排手册，边教你边演示怎么建会话、派活、收报告。

### 6.2 用 SMA 跑一轮任务的完整流程

1. 新建会话，模板选 **SMA(NoAdapter)**（UI 新建弹窗 → Session Template，见 §4.2）；
2. 把目标交给它，例如：「并行调研方案 A / B / C，分别给出结论，最后汇总一份对比报告」；
3. SMA 先做**决策三问**（能真并行吗？拆了更快吗？精度关键吗？）判断拆不拆；
4. 决定拆解后，SMA 用 pan MCP 工具自动完成：
   - `session_create` 建子会话（**自动归管**，管理关系无需你手动 claim）；
   - `worker_assign` 给每个子会话异步派发任务；
   - `report_subscribe` 订阅完成报告；
5. 各子 Worker 完工，报告自动投进 SMA 的收件箱 `queue_pending`；
6. SMA 汇总、trust-but-verify 验收后，向你交付一份合并结果；
7. 收尾：SMA 用 `session_batch_delete` 批量清理子会话（订阅自动随删）——不用你管。

> SMA 的行为细节（决策三问、并行 fan-out、串行依赖等）见第 8 章「编排实践：Meta-Agent 指南」；本章只讲你该怎么用 SMA。

### 6.3 什么时候可以不走 SMA（直连）

| 场景 | 建议 |
|------|------|
| 简单即时任务（问个问题、改一行） | 直接建普通会话发消息即可，不必绕 SMA |
| 想实时盯某个长任务的过程输出 | 直连该会话，边看边跟进 |
| 多任务并行 / 需要汇总交付 / 长期协作 | 交给 SMA——这是它的本职 |

### 6.4 任务描述怎么写

- **给 SMA 的话**：目标 + 边界 + 验收标准。拆解、建子会话、写子任务由 SMA 负责，你不用替它规划；
- **SMA 派给子会话的话**：由 SMA 按「新会话要自包含（背景/目标/涉及文件/验收标准），已有上下文给简短指令」的规则处理——你不需要直接面对子会话；
- 判断一个会话有没有上下文：UI 里看聊天记录是否已有内容。

### 6.5 怎么验收

- 你验收的是 SMA 的**交付物**（合并报告），不是每个子会话；
- 抽查机制：UI 会话列表里所有子会话都显示为 SMA 的 managed（被管理）状态，可点开看各自结果；
- 重要改动让 SMA 附上验证步骤（跑测试/命令），你在 Editor 视图检查改动（§4.4）。

### 6.6 什么时候人工接管

- SMA 反复绕圈、拆解不合理：直接跟 SMA 对话纠正，它会重新拆解；
- 某个子 Worker 卡死：watchdog 会回收，SMA 也会用 send_force 打断；真需要手动时对子会话 Takeover（§4.2）；
- SMA 自身需要检修：Takeover 它的终端，处理完 restart 继续。

### 6.7 人设、记忆与长期会话

- 给 SMA 主会话挂 **Character + Memory**（§1.2）：它会长期记住你的偏好与项目背景，越用越顺手；
- 需要长期复用的专业会话（如带人设的对话角色）：单独建，让 SMA 认领管理；SMA 按需调用，你不需要直接操作它们。

### 6.8 会话组织与清理

- 子会话几乎不用你管：SMA 建、SMA 订阅、SMA 删（`session_batch_delete`）；
- 你只需要维护：SMA 主会话（长期保留）+ 极少数需要长期复用的会话；
- 提醒：删除会话不删 workdir 磁盘目录（§4.5），需要保留的产物在删除前先落盘。

---

## 7. 核心操作详解

本章解释各操作的含义与适用场景（操作入口：UI 见第 4 章，HTTP/MCP 见第 12 章开发者参考）。

### 7.1 派活三式

| 操作 | 语义 | 适用 |
|------|------|------|
| assign | **异步派新任务**：立即返回 queued，无活 Worker 自动 spawn；`taskId` 幂等（同 id 重发不双跑） | 新任务 / 并行 fan-out（默认首选） |
| send | 向已有 Agent 发消息：**排队不打断**，目标空闲才处理；无活 Worker 入持久队列 | 多轮追问 / 补充线索 |
| send_force | **强制推送 = restart + send**：打断进行中任务立即送达 | 方向变更 / 紧急指令 / Worker 卡死兜底 |

### 7.2 生命周期

- **spawn**：启动 Worker（一个 Agent 同时只有一个 Worker，已有则先 kill）；
- **kill**：杀 Worker 进程，Session 数据保留（进程是顺带的，随时可重建）；
- **restart**：杀进程后带 resume 重建；
- **interrupt**：中断当前任务（仅运行中）。

### 7.3 归属关系：claim / unclaim

- **claim**：建立「主管 ↔ 会话」双向管理关系，**claim 自动订阅报告**；目标已被他人管理则拒绝；
- **unclaim**：解除管理并自动退订报告；
- 每个 Session 只属于一个主管（星形拓扑）；可查上级管理链。

### 7.4 branch（分支）

从现有会话 fork 出独立副本：继承设置与 MCP 绑定，与原会话互不影响。适合「试另一条路」——试错了删掉分支即可，不影响主线。

### 7.5 takeover（人工接管）

restart Worker 后在**新终端窗口**打开 adapter 原生交互式 CLI（`--resume` 恢复上下文），Worker 状态置 `held`。held 期间任务投递被拒、watchdog 跳过。恢复：restart 清 held。

### 7.6 handoff（替身交接）

场景：上下文过大需精简，或中途想换 adapter（普通会话不能直接换）。创建孪生会话 B 接替 A：B 接管 A 的全部关系网（managed、订阅、QQ 绑定），A 归档为 `(archive) <原名>`；只带精简摘要，**避免长会话上下文膨胀**。

### 7.7 批量删除

多选批量删除并清理跨会话引用。注意：删除不删 workdir 磁盘目录；底层 CLI 会话仍在，可随时重新导入复用。

### 7.8 QQ 订阅

把某 QQ 会话（好友/群）订阅到 Pan 会话：QQ 新消息进收件箱时以 `@@@@by qq` 提醒推入 `queue_pending` 并唤醒 Worker（配合 §9.2 使用）。

### 7.9 多 CLI（Adapter）

每种 CLI Agent 一个 adapter：`cbc` / `kimi` / `opencode` / `claude` / `codex`。新建会话时选哪个就用哪个干活；同一个任务想换 CLI 用 §7.6 handoff（普通会话不能中途换 adapter）。适配细节（执行模式、MCP 注入方式）见 §12.3。

---

## 8. 编排实践：Meta-Agent 指南

本章面向「用 Pan 当调度台，让一个主管指挥多个 Worker 并行干活」的使用者。

### 8.0 先给 Agent CLI 装上 pan skill（强烈建议）

**pan skill**（`SKILL.md`）是给「想当 Meta-Agent 主管」的 agent 准备的**冷启动手册**：它把 Pan 的编排链路（`session_create → report_subscribe → agent_assign → queue_pending`）、MCP 工具约定与踩坑一次性教给 agent。配上之后，agent 开工即自动掌握这些知识，**不需要你在提示词里从头教**；再配合 MCP 工具注入，agent 就能直接上手调度 Worker。

配置方式：

- **主源**：`docs/skills/pan/SKILL.md`（git 跟踪，随仓库更新，改动以它为准）；
- **CodeBuddy（cbc，主力 adapter）**：本仓库已内置项目级副本 `.codebuddy/skills/pan/SKILL.md`，用 CodeBuddy 在本仓库 workdir 里干活时**自动加载，无需额外操作**；在其它项目里用，把整个 `pan/` 目录复制到目标项目的 `.codebuddy/skills/` 下即可；
- **其它支持 Agent Skills 的 CLI**（如 Claude Code 的 `.claude/skills/`、Codex 的 `~/.codex/skills/` 等）：把 `pan/SKILL.md` 按该 CLI 的 skill 目录约定放好。frontmatter 的 `name` / `description` 是 skill 的元信息（description 影响触发时机，建议保留原名）。

### 8.1 决策三问

派发前自问：① 能真并行吗？② 拆了更快吗？③ 精度关键吗？任一不过 → 自己做；全过 → 并行派发。

### 8.2 并行 fan-out 主链路

```
session_create → report_subscribe（订阅）→ agent_assign × N → queue_pending 收报告 → session_get 汇总 → session_delete 收尾
```

- `agent_assign` 立即返回 queued，**不需要手动轮询**；
- 完成通知只有一条编排路径：MCP `report_subscribe` → 报告落盘到 meta-agent 的 `queue_pending`（跨服务重启不丢）；
- 外部 WS 盯梢（`/ws/agent`、`packages/mcp/monitor_workers.py`）仅供测试/排障/外部协调者；
- 传 `task_id`（uuid 样幂等键）保证重试不双跑；
- zombie 通知：被管 Session 的 Worker 在任务进行中异常死亡时，收 `{"status":"error","type":"zombie",...}` 报告（正常完成后的 idle 回收不报）。

### 8.3 trust-but-verify 验收

合并汇报前逐项核对改动、跑测试验证。读结果：`session_get(session_id)` 的 `lastResult.status`（`queued`/`running`/`done`/`error`/`pending`）与 `result` 字段。

### 8.4 worktree 并行

多个 Worker 共改一个项目时，让所有 Session 的 `workdir` 用**绝对路径**指向同一项目目录（或各自独立 git worktree），避免提交冲突。`workdir` 默认 `data/workdirs/<name>`（相对基准 = 实际运行的那个 Pan 服务实例的数据根，以 `session_create` 返回的 `workdir` 字段为准）。

### 8.5 串行依赖

阻塞式 handoff 已移除（2026-08-26）。串行 = 派发后订阅报告，报告入 `queue_pending` 即「下一步」的信号——「等」是 meta-agent 的 idle 状态，而非阻塞调用。

### 8.6 清理

完成后 `session_delete` / `session_batch_delete` 释放进程与磁盘；watchdog 只回收进程不删 Session。不再需要的会话及时清理。

---

## 9. 通道：Web / QQ / Remote

### 9.1 Web

主通道：Dashboard（第 3/4 章）+ `/ws` + HTTP API（§12.2）。

### 9.2 QQ（QQ Bridge）

用 QQ 遥控 Pan：在 QQ 里给 Bot 发消息，消息变成 Worker 的指令。

- 依赖：`packages/qq/requirements.txt`（nonebot2 + onebot 适配器 + httpx），跑在**独立解释器**（NoneBot 不装项目 .venv；`setup.bat` 探测后写入 `qq.python`）。
- 网关：NapCat（正向 WS，端口 3001）或 LLOneBot（3002），`qq.channel` 选择；WS 地址写 `packages/qq/.env` 的 `ONEBOT_WS_URLS` 或 config 的 `qq.<channel>.ws_urls`。
- 启动：`python main.py` 按 `qq.enabled` 自动拉起/终止 QQ bot（PID 写 `data/qq_bot.pid`）；NapCat 不可达时降级运行（每 3s 重连）。
- 模式：`mirror`（收到消息自动建 Session 并回复）/ `selective`（消息只进 inbox，meta-agent 经 pan-qq MCP 处理）。
- 编排接入：`session_qq_subscribe`（§7.8）收 inbox 提醒；`manifest.json` 的 `command_routes` 可声明 QQ 前缀命令直发外部 HTTP API（不走 LLM）。

### 9.3 Remote（Cloudflare Tunnel）

出门在外，公网访问调度台：

```bash
python -m packages.remote        # 或 scripts/start_cf.ps1
```

`remote.quick_tunnel=true` 输出临时 `*.trycloudflare.com` URL；`false` 需 `remote.config_path` 指向 named tunnel 的 config.yml（公网域名取其 `ingress.hostname`）。状态服务 `curl http://127.0.0.1:8769/status`。隧道转发 Pan 主端口——公网侧同样**无鉴权**（§11）。

---

## 10. 故障排查

| 现象 | 原因与处理 |
|------|-----------|
| Worker 状态点消失 / 变 `null` | watchdog 已回收（idle/静默/任务超时）。直接再发任务或点 Start 自动重建并恢复上下文（Session 数据完好） |
| 任务长时间无回复 | 查顶栏状态：`idle` = 已完成未读（看聊天流即可）；`running` 且超时可能已被回收。回收只杀进程不删 Session |
| 超时配置不生效 | `worker.*` 改后需重启或热重载（UI 全局设置或 `POST /api/config/reload`，scope `worker`） |
| 队列不消费 | `queue_pending` 非空但无活 Worker 时全局 watchdog（30s tick）会自动拉起；持续不动查 `data/logs/pan.log` 的 watchdog/branch 日志 |
| 端口占用 | 换 `port` 或 `PAN_PORT`；确认旧实例已 `stop_pan.bat` 树杀 |
| 启动后出现 `ses_a` / `ses_os` / `ses_d` / `ses_f` 等测试会话 | 这些固定 ID 来自测试夹具。测试若把 `Session` 写入正式 `data/sessions/`，启动时会被 `sess.list_all()` 加载；其中遗留的 `queue_pending` 还会被全局 watchdog（约 30s）当作待恢复任务自动 spawn。当前 `tests/conftest.py` 已为 `pytest tests/` 下的每个测试使用独立临时 Session 目录，正常测试不会再污染正式数据。清理旧残留时只删除已确认的测试文件，不要按 `ses_*` 全量删除。 |
| 聊天里看不到输出 | 检查消息区是否滚动到底部或面板是否折叠；tool 行需点击展开（§4.3） |
| QQ 连不上 | 查 NapCat/LLOneBot 是否启动、`ONEBOT_WS_URLS` / `qq.<channel>.ws_urls` 是否指向正确端口（3001/3002）；QQ bot 崩溃看 `data/logs/pan.log` 启动告警（Pan Core 不受影响） |
| 带 character 的会话首个任务卡顿 | embedding 首次加载/网络重试；等 15s 超时降级，或 `memory.enabled: false` |
| Worker 报 "Worker process dead" | 进程已崩溃/被回收，重新 Start / 再发任务（自动恢复上下文） |
| 删除 Session 后 workdir 残留 | 设计如此（delete 不删磁盘目录），需要时手动清理；CLI 原生会话可重新导入复用 |
| 新用户访问 `/vanilla` 空白 | 旧版 Vanilla 前端已弃用且未构建（需项目根 `npx tsc`）；推荐使用 `/react/` |

---

## 11. 安全与运维提示

- **API 无鉴权**，默认绑 `127.0.0.1` 是有意设计。改 `PAN_HOST` 为非 loopback 会把全部端点暴露到网络（启动时打印告警）。`pan_access` 隔离只在 MCP 层生效，HTTP/前端是最高权限。
- **config.json 已 gitignored**：端口、QQ token（`ONEBOT_ACCESS_TOKEN`）、`remote.config_path` 等都在其中，不要提交；凭据也不进代码库。
- **数据落盘位置**（均相对项目根）：`data/sessions/`（Session 元数据 + `.history.jsonl`）、`data/workdirs/`（默认工作目录）、`data/mcp-configs/`（每会话 MCP 配置）、`data/characters/`、`data/memory/`（SQLite 记忆库）、`data/logs/pan.log`、`data/qq_bot.pid`。备份/迁移按目录整体拷贝。
- **Memory 依赖降级**：`minimal-requirements.txt` 不含 ML 链；向量检索需 `sentence-transformers`，缺失时懒加载降级不影响 Core；`jieba` 缺失会降低中文检索质量。
- **Remote 公网暴露**：Cloudflare Tunnel 侧无鉴权，公网可访问全部 API——仅在理解风险时开启，建议叠加 Cloudflare Access 等外部防护。
- **git worktree 场景**：worktree 无独立 `.venv`，统一用主仓库解释器。

---

## 12. 开发者与 API 参考

> 本章面向开发/集成场景（脚本、外部 Agent、自定义前端）。普通用户通常不需要；日常操作请走第 3/4 章界面。

### 12.1 MCP 工具层

#### 12.1.1 接入方式

**方式 A：Session 内自动注入（推荐）**——创建 Session 时指定 `mcpServers: ["pan"]`（或用 SMA 等自带 MCP 的模板），adapter 在 spawn 时自动生成 `data/mcp-configs/<session_id>.mcp.json` 并经 `--mcp-config` 注入，同时写入 `PAN_AGENT_SESSION_ID` / `PAN_AGENT_SESSION_TITLE` 环境变量（工具据此识别调用方身份）。无本地 `config.json` 时，Pan 默认加载根 `manifest.json` 与 `packages/mcp/manifest.json`：前者提供项目模板/角色，后者提供 `pan` 与 `pan-qq`。MCP stdio command 使用 `${PAN_PYTHON}`，优先取环境变量，否则使用运行 Pan 的 Python 解释器，因此不依赖 worktree 私有 `.venv`。各 adapter 注入方式：cbc/claude 写 `--mcp-config`；kimi 写会话级隔离 home（`--kimi-home`）；opencode 写项目级 `opencode.json`；codex `-c mcp_servers.*` 内联注入。选择未知或不可用的 MCP server 会返回明确错误，不会静默启动一个缺少 MCP 的 worker。

**方式 B：独立进程接入（任意 MCP 客户端）**：

```bash
# stdio（本地 CLI 客户端，在 .mcp.json / --mcp-config 里声明 command）
PAN_API_URL=http://127.0.0.1:8768 python -m packages.mcp.server --transport stdio

# SSE / streamable-http（远程或多客户端，默认端口 9740，路径 /sse）
python -m packages.mcp.server --transport sse --port 9740
```

后端地址优先级：`--pan-url` 参数 > `PAN_API_URL` 环境变量 > `http://127.0.0.1:8768`。独立进程没有 `PAN_AGENT_SESSION_ID`，依赖身份的工具（claim / report_subscribe / manager_chain 等）不可用。

> **三对齐**：MCP server 目标端口（`PAN_API_URL`）必须与 `PAN_AGENT_SESSION_ID` 所在 Pan 实例同端口，否则 `report_subscribe` / `qq_bind` 失效。

#### 12.1.2 `pan` server 工具清单（35 个）

命名分层：`agent_*` 是一等工具（以 session_id 寻址，无活进程也容忍）；`worker_*` 是兼容别名（DEPRECATED），仅 `worker_id` 进程寻址为遗留独有路径，新代码一律用 `agent_*`。

**会话管理（15）**

| 工具 | 关键参数 | 说明 |
|------|----------|------|
| `session_create` | `name`（必填，唯一），`adapter?`/`model?`/`permission_mode?`/`workdir?`/`session_template?`/`character_id?`/`system_prompt?`/`pan_access?` | 创建会话（不 spawn）；workdir 默认 `data/workdirs/<name>`，Pan 外用绝对路径 |
| `session_import` | `action`（`list_projects`/`list_workspaces`/`list_sessions`/`import`），`adapter?`，`cwd?`/`project_dir?`，`session_id?` | 导入外部 CLI 历史会话（cbc/kimi/opencode/claude/codex）；仅建 Session 不 spawn；claude/codex 可用通用 provider 端点，`cwd` 可选 |
| `session_list` | `summary?` | 列出全部会话；`summary=true` 只返回精简字段（巡检首选，避免全量 history 撑爆输出） |
| `session_managed` | — | 调用者管理的 session 摘要（需 `PAN_AGENT_SESSION_ID`） |
| `manager_chain` | — | 调用方的上级 manager 链 |
| `session_get` | `session_id`，`limit?` | 详情（history + lastResult） |
| `session_update` | `session_id`，`model?`/`permission_mode?`/`always_thinking_enabled?`/`effort?`/`max_thinking_tokens?`/`mcp_servers?`/`game_id?` | PATCH 封装；改 mcp_servers 返回 `requireRestart: true`（idle worker 自动 respawn 生效） |
| `session_delete` | `session_id` | 删除并 kill worker（不删 workdir） |
| `session_batch_delete` | `session_ids` | 批量删除（逐个过 managed 隔离检查） |
| `session_handoff` | `session_id`，`handoff_prompt`（必填），`copy_settings?`(=true)，`adapter?`/`model?`/`permission_mode?` | 替身交接（§7.6） |
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

#### 12.1.3 `pan-qq` server 工具（6 个，`packages/qq/mcp.py`）

`qq_send_message` / `qq_read_conversation` / `qq_read_inbox` / `qq_list_contacts` / `qq_bind` / `qq_unbind`。selective 模式下 meta-agent 用它做 QQ 选择性收发；`qq_bind` 后新消息以 `@@@@by qq` 提醒推入 `queue_pending`。SMA 模板已默认挂载。

#### 12.1.4 安全模型（MCP 层）

无传统鉴权，靠「身份注入 + managed 隔离」：Session 的 `pan_access` 三能力位 `restrict_to_managed` / `can_claim_unmanaged` / `auto_claim_created`（默认全 False）。受限调用方操作他人 Session 会被 `permission_denied`；spawn/task/assign/send 自带「派任务即接管」。注意这些限制**只在 MCP 层实施**，HTTP API 不检查（见 §11）。

### 12.2 HTTP/WS API

基址 `http://127.0.0.1:<port>`；全部返回 JSON，失败多为 HTTP 200 + `{"error": "..."}`。完整 69 端点清单见 README「API 概览」；本章给主要端点与调用示例。**请求 body 用 camelCase，MCP 参数用 snake_case**（如 HTTP `sessionId` ↔ MCP `session_id`；创建响应里叫 `id`，后续请求体一律用 `sessionId`）。

最小链路（等价的界面操作见第 3 章）：

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

#### 12.2.1 Session 管理

| 方法+路径 | 用途 |
|-----------|------|
| `GET /api/sessions`（`?summary=1`） | 列出全部（summary 精简；全量 history 截最后 50 条） |
| `POST /api/sessions` | 创建（body：`name`/`adapter`/`model`/`permissionMode`/`workdir`/`sessionTemplate`/`systemPrompt`/`alwaysThinkingEnabled`/`effort`/`maxThinkingTokens`/`outputMode`/`panAccess`/`characterId` 等，均可省略） |
| `GET /api/sessions/{id}` | 详情（`lastResult`/`workerStatus`/`managedBy`/`reportSubscriptions` 等） |
| `GET /api/sessions/{id}/history?limit=50&before=<游标>` | 历史分页 |
| `PATCH /api/sessions/{id}` | 更新设置（model/effort/MCP 等；idle Worker 自动 respawn，running 标 `pending_restart`） |
| `POST /api/sessions/{id}/rename` | 重命名（body `{"name"}`；同步回写 adapter 原生存储） |
| `POST /api/sessions/{id}/branch` | fork 分支（§7.4） |
| `POST /api/sessions/{id}/handoff` | 替身交接（§7.6） |
| `DELETE /api/sessions/{id}` / `POST /api/sessions/batch-delete` | 删除 / 批量删除 |
| `GET /api/sessions/{id}/managers` | manager 链 |

#### 12.2.2 Worker 与任务投递

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

#### 12.2.3 编排端点

`POST /api/assign`、`POST /api/send`（`force:true` 即强制）、`POST /api/claim` / `POST /api/unclaim`（body `{"managerId","sessionId"}`）、`POST /api/report-subscribe` / `POST /api/report-unsubscribe`——语义见 §7/§8。

#### 12.2.4 导入 / 设置 / Manifest / Memory / 文件

| 类别 | 端点 |
|------|------|
| 通用导入 | `GET /api/adapters/{adapter}/sessions`、`POST /api/adapters/{adapter}/sessions/import`（claude/codex 走此端点） |
| cbc/kimi/opencode 导入 | `GET /api/cbc/projects`、`GET /api/cbc/sessions`、`GET /api/cbc/browse`、`POST /api/cbc/sessions/import`；`GET /api/kimi/workspaces`、`GET /api/kimi/sessions`、`POST /api/kimi/sessions/import`；`GET /api/opencode/sessions`、`POST /api/opencode/sessions/import` |
| 模型/Adapter | `GET /api/models?adapter=cbc`、`GET /api/adapter/config?adapter=cbc`、`GET /api/adapters`、`GET /api/cli/status`（Agent CLI 可用性诊断） |
| 设置 | `GET`/`PUT /api/settings/ui`（App Settings 显示设置）；`POST /api/config/reload`（config.json 热重载，`{"scope":"adapters"\|"worker"\|"all"}`）；`POST /api/manifest/reload`（manifest 热重载） |
| 模板/MCP | `GET /api/session-templates`（`GET /api/characters/profiles` 为其废弃别名）、`GET /api/mcp/servers`、`GET /api/manifest/command-routes` |
| Character | `GET`/`POST /api/characters`、`GET`/`DELETE /api/characters/{id}` |
| Memory | `POST /api/memory/index`、`GET /api/memory/search?q=`、`GET /api/memory/stats`、`POST /api/memory/inject` |
| 文件系统 | `GET /api/fs/list`、`GET /api/fs/read`、`POST /api/fs/write`、`POST /api/fs/rename`、`POST /api/fs/delete`（限 session workdir 内，拒绝 `..` 逃逸，单文件 5 MiB 上限） |
| QQ | `POST /api/qq/subscribe`、`POST /api/qq/unsubscribe`、`POST /api/qq/notify`、`GET /api/qq/contacts` |

#### 12.2.5 WebSocket

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

### 12.3 多 CLI 适配

Adapter 协议（`packages/core/adapters/base.py`）+ 注册表（`registry.py`）。五种内置 adapter：

| Adapter | CLI | 执行模式 | Resume/Fork | MCP 注入 | 备注 |
|---------|-----|----------|-------------|----------|------|
| `cbc` | CodeBuddy CLI | stream + oneshot（唯一双模式） | ✔ / ✔（`--fork-session`） | `--mcp-config` | 主力 adapter；原生 JSON 流协议 |
| `kimi` | Kimi CLI | stream（wrapper 长驻） | ✔ / ✔ | 会话级隔离 home（`--kimi-home`） | 思考模式由自身 config.toml 控制 |
| `opencode` | OpenCode CLI | stream（wrapper） | ✔ / ✔ | 项目级 `opencode.json` | |
| `claude` | Claude Code CLI | stream + oneshot | ✔ | `--mcp-config` + `--permission-prompt-tool` | 默认长驻 `--input-format stream-json`，支持 steer/`/compact`；显式 `outputMode: "oneshot"` 时逐条 `claude -p` |
| `codex` | OpenAI Codex CLI | stream（原生 app-server 桥接） | ✔ | `-c mcp_servers.*` 内联（零文件污染） | 原生 thread/turn、增量输出与中断 |

执行模式：`stream` 长驻进程（消息写 stdin，可挂 MCP）；`oneshot` 每任务起一次性进程（`outputMode: "oneshot"` 时启用，仅 adapter 声明支持时可选）。特殊行为详见 `docs/references/cli-adapter-special-behaviors.md`。

前端说明（双前端维护约定）：**React Dashboard 是当前唯一维护并推荐的前端**（源码 `packages/web/src/` → `pnpm build`）；旧版 Vanilla 前端已弃用（deprecated），源码 `packages/web/ts/app.ts` → 项目根 `npx tsc` 编译，产物均 gitignored、禁止直改；`/vanilla` 路由仍可访问作后备，但不建议新用户使用。

---

## 13. 关联文档

- `README.md` — 项目概览、卖点、69 端点 API 索引
- `docs/skills/pan/SKILL.md` — 编排知识单一事实源（Meta-Agent 冷启动手册；MCP 内 `pan_handbook` 返回其全文）
- `docs/skills/pan/references/http-api.md` / `ws-protocol.md` — HTTP/WS 技术细节与轮询兜底策略
- `docs/references/cli-adapter-special-behaviors.md` — 各 CLI 特殊行为
- `importantInfo.md` — 端口与启动顺序速查
