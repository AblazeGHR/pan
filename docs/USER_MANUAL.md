# Pan 用户手册

> 给第一次接触 Pan 的用户：从安装、创建第一个 Session，到让 Meta-Agent 自动派活、收报告和交付结果。本文以当前 React Dashboard、`packages/mcp/server.py`、`packages/web/server.py`、`packages/core/worker.py` 和根目录 `manifest.json` 的实际行为为准。

**[English](./USER_MANUAL.en.md) · 中文**

## 目录

1. [先用一句话理解 Pan](#1-先用一句话理解-pan)
2. [安装、启动和端口](#2-安装启动和端口)
3. [第一次操作：新建会话并派出任务](#3-第一次操作新建会话并派出任务)
4. [两种获得 Meta-Agent 能力的方式](#4-两种获得meta-agent能力的方式)
5. [Dashboard 界面操作](#5-dashboard-界面操作)
6. [Manage、managedBy 与父子关系](#6-managemanagedby-与父子关系)
7. [Subscribe、报告和 queue_pending](#7-subscribe报告和-queue_pending)
8. [pan_access 权限](#8-pan_access-权限)
9. [四种派发工具如何选择](#9-四种派发工具如何选择)
10. [Worker 生命周期与 watchdog](#10-worker-生命周期与-watchdog)
11. [worktree 并行开发与交付](#11-worktree-并行开发与交付)
12. [MCP、模板和端口对齐排障](#12-mcp模板和端口对齐排障)
13. [安全、清理与常见问题](#13-安全清理与常见问题)
14. [开发者速查](#14-开发者速查)

---

## 1. 先用一句话理解 Pan

Pan 是一个把多个 CLI Agent 组织成“一个主管、多个执行者”的编排平台。你可以只和一个 Meta-Agent（也叫 SMA，Super Meta-Agent）对话；它把目标拆成子任务，创建或复用会话，异步派发给不同的 Agent，收到完成报告后验收并汇总。

### 1.1 五个容易混淆的名词

| 名词 | 它是什么 | 生命周期与用途 |
|---|---|---|
| Agent | Pan 中可被编排的逻辑对象 | 实际上以一个 `Session` 身份存在，后续用 `session_id` 找它 |
| Session | 持久化的会话容器 | 保存对话历史、模型、adapter、`workdir`、管理关系、待处理队列；ID 形如 `ses_...` |
| Worker | Session 名下实际运行的 CLI 子进程 | 临时的物理执行体；可以被 watchdog 回收，也可以重新 spawn，不等于删除 Session |
| Adapter | Pan 对某种 CLI 的适配器 | 当前注册表包括 `cbc`、`kimi`、`opencode`、`claude`、`codex`；是否可用以 Dashboard 的 CLI 检测为准 |
| Meta-Agent / SMA | 主管角色，不是另一种进程 | 一个拥有 Pan MCP 工具和编排权限的 Session；负责拆解、派发、订阅报告、验收和汇总 |

可以把它想成：Session 是员工档案，Worker 是今天上班的进程，Adapter 是员工使用的工具类型，Meta-Agent 是项目经理。Worker 死了，档案和历史仍在；重新启动时 adapter 会尝试恢复已有 CLI 上下文。

### 1.2 什么时候用哪一种

- 只问一个问题、改一行代码：创建普通 Session，直接聊天即可。
- 多个模块并行、需要统一验收、任务较长：创建 SMA，让它管理子 Session。
- 已经有一个外部 Agent CLI：给它挂 `pan` MCP，再安装 `docs/skills/pan/SKILL.md` 这份 skill，它就能成为 Meta-Agent。

---

## 2. 安装、启动和端口

### 2.1 安装和启动

至少准备一个 Pan 支持的 CLI，并在启动 Pan 的同一用户环境中确认命令可用：

```powershell
cbc --version
kimi --version
opencode --version
claude --version
codex --version
```

Windows：

```powershell
pip install -r minimal-requirements.txt
Copy-Item config.example.json config.json
Set-Location packages/web
pnpm install
pnpm build
Set-Location ../..
python main.py
```

然后打开 <http://127.0.0.1:8768>。已有配置的用户也可以使用 `scripts/setup.bat`、`scripts/start_pan.bat` 和 `scripts/stop_pan.bat`。

macOS/Linux 可使用：

```bash
bash scripts/setup.sh
bash scripts/start.sh
```

### 2.2 端口和三对齐

| 端口/变量 | 用途 |
|---|---|
| `8768` | main 分支 Pan 主服务默认端口 |
| `8767` | test 分支约定端口 |
| `PAN_PORT` | 覆盖主服务端口 |
| `PAN_API_URL` | MCP server 访问 Pan 主服务的地址，默认 `http://127.0.0.1:8768` |
| `PAN_PYTHON` | manifest 中 stdio MCP server 使用的 Python 解释器 |
| `9740` | MCP server 的 SSE/streamable-http 默认端口，不是 Pan 主服务端口 |

使用 `report_subscribe` 时必须三者一致：MCP 配置中的目标地址、`PAN_API_URL`、`PAN_AGENT_SESSION_ID` 所属的 Pan 实例必须是同一服务（同一端口）。把 MCP 指到 8768、而调用者 Session 实际在 8767，会出现“找不到 manager”、订阅无效或 404。

---

## 3. 第一次操作：新建会话并派出任务

这是普通用户不需要 MCP 的最短路径。

1. 打开 Dashboard，点击左侧顶部 **New**，或打开新建配置弹窗。
2. 填写会话名称，例如 `first-task`。
3. 选择当前可用的 **Adapter**；如果没有 CLI 可用，创建后也无法运行 Worker。
4. 可选地选择 **Session Template**；不选时使用系统默认配置。
5. 可选地在 **Workdir** 中选择服务端目录。默认目录是实际运行该 Pan 实例的数据根下的 `data/workdirs/<name>`，以创建响应返回的 `workdir` 为准。
6. 创建后选中该 Session，点击顶栏 **Start**。Start 只启动 Worker；若直接在输入框发送，当前实现也会在没有活 Worker 时自动启动。
7. 在底部输入框输入自包含任务，按 Enter 发送；Shift+Enter 换行。例如：

```text
请检查 src/utils.py 中的空指针问题，修复它，运行相关单元测试，并在最后告诉我修改了哪些文件和测试结果。
```

8. 等待聊天区出现结果。状态点通常会从 running 回到 idle；之后可以继续追问、在 **Editor** 查看文件，或右键会话选择 **Delete**。

[图片占位：pan-user-01-create-session.png]
说明：截图 Dashboard 新建会话弹窗，展示 Name、Adapter、Session Template、Workdir、Create 按钮；让新用户知道第一次创建需要填什么。建议插入位置：本节第 2 步之后。

---

## 4. 两种获得 Meta-Agent 能力的方式

两种方式的共同点都是：最终得到一个带 Pan MCP 的 Agent。区别是“能力从哪里来”：模板一次性预置；已有 Agent 则由你手工接线和安装规则。两者可以结合，但不要把“模板名”误认为“任何 Agent 自动拥有编排能力”。

### 4.1 方式一：通过 Session Template 创建（推荐新用户）

模板定义在根目录 `manifest.json` 的 `session_templates` 数组中。当前与 SMA 相关的模板是 `SMA(NoAdapter)` 和 `SMA(cbc)`。前者不预先锁定 adapter，后者固定使用 `cbc`。

两个 SMA 模板都设置了 `mcp_mode: "always"`，挂载 `pan` 与 `pan-qq`，并提供 SMA system prompt；当前默认 `pan_access` 是：

```json
{
  "restrict_to_managed": false,
  "can_claim_unmanaged": true,
  "auto_claim_created": true
}
```

在 Dashboard 中选择 **New → Session Template → SMA(NoAdapter)**，确认 adapter、模型和工作目录后创建。创建完成后直接向它说：

```text
请先告诉我你能怎样使用 Pan。然后帮我把“检查项目测试并修复失败项”拆成合适的子任务，完成后汇总并附上验证结果。
```

SMA 会自行决定是否值得拆解；并行时典型链路是：`session_create → report_subscribe → agent_assign → queue_pending → session_get`。

模板字段的应用顺序是“创建时显式字段 > 模板值 > 系统默认值”。`mcp_mode=always` 会锁定 MCP 开关；`never` 会锁定为关闭；`optional` 可选。模板是配置基线，不是一个独立的 Agent 类型。

[图片占位：pan-user-02-create-sma.png]
说明：截图新建弹窗中选中 `SMA(NoAdapter)` 的状态，展示模板名、MCP 标记、adapter 可选项和 Create 按钮；让用户理解模板会预置主管能力。建议插入位置：本节方式一操作说明之后。

### 4.2 方式二：给已有 Agent 配置 pan MCP + pan skill

适用于你已经在外部 CLI 或其他 Agent 容器中有一个长期 Agent，希望它反过来管理 Pan。

第一步，让这个 Agent 能调用 Pan MCP。最简单的完整方式是让 Pan 为 Session 自动注入：创建 Session 时启用 `mcpServers: ["pan"]`（或在 Dashboard 的 Manage → MCP Server 勾选 `pan`）。Worker spawn 时，adapter 会生成 `data/mcp-configs/<session_id>.mcp.json` 并注入到 CLI；不同 adapter 的注入方式由 adapter 自己处理，不要手工猜参数。

如果是独立 MCP 客户端，可运行：

```powershell
$env:PAN_API_URL = "http://127.0.0.1:8768"
python -m packages.mcp.server --transport stdio
```

第二步，把 `docs/skills/pan/SKILL.md` 安装到该 Agent CLI 的 skill 目录，并确保它能读到这份文件。该 skill 规定了术语、异步主链、报告队列、权限边界和排障动作；只有 MCP 没有 skill，Agent 可能会误用工具或持续轮询。

第三步验证：让 Agent 调用 `pan_handbook()`；再用 `session_list(summary=true)` 查看精简列表；对一个测试 Session 执行 `report_subscribe` 后再 `agent_assign` 一个小任务。看到 `queued`，并能在完成后收到报告，才算接线完成。

独立进程没有 `PAN_AGENT_SESSION_ID`，所以 `session_claim`、`report_subscribe`、`manager_chain` 等需要调用者身份的工具不可用。想获得完整主管能力，应让它作为 Pan 管理的 Session 运行，使 adapter 注入身份。

### 4.3 两种方式如何选择

| 情况 | 建议 |
|---|---|
| 第一次用 Pan、没有现成 Agent | 选 `SMA(NoAdapter)` |
| 已有固定 CLI 和想快速建立主管 | 选 `SMA(cbc)` 或已有 Session 勾选 `pan`，再安装 skill |
| 外部 Agent 只想查询/派任务 | 独立 MCP 可用，但身份型工具和报告订阅受限 |
| 要让已有 Agent 真正自主编排 | MCP + `pan` skill + Pan 管理的 Session，三者都要有 |

---

## 5. Dashboard 界面操作

左侧 Sidebar 是会话列表；Chat 与当前 Session 对话；Editor 浏览和编辑当前 Session 的 `workdir`；顶栏有 **Start、Restart、Interrupt、Takeover、Kill**。右键会话菜单包含当前可见的 Rename、Branch、Manage、Postbox、Delete 等操作。

### 5.1 Manage 面板

**Manage** 打开 `Manage Sessions`，不是打开 Worker 终端，而是管理关系、完成报告订阅、MCP 权限和 MCP server 的面板。它有四部分：

1. **Managed by / 被谁管理**：显示当前 Session 的父 manager，可以解除父子关系。
2. **Manages / 管理谁**：点击 **Manage** 认领，点击 **Managed** 解除；同一行的 **Subscribe** 独立控制完成报告。
3. **Pan Access / MCP 权限**：编辑三个权限开关，只影响 MCP 调用。
4. **MCP Server / MCP 服务**：从 manifest 声明的服务中选择当前 Session 要挂载的服务。变更通常需要 Worker 重启才在 CLI 中生效；模板可能锁定选择。

[图片占位：pan-user-03-manage-sessions.png]
说明：截图 Manage Sessions 面板完整四个区域，清楚展示 Managed by、Manages 行内的 Manage/Subscribe、Pan Access 三个开关和 MCP Server 列表。建议插入位置：本节末尾。

### 5.2 Postbox 不等于报告订阅

右键菜单里的 **Postbox** 是 QQ 会话收件箱订阅：选择 QQ 联系人后，QQ 新消息可以进入该 Session 的队列。它不是 `report_subscribe`，也不会订阅 Worker 完成报告；没有使用 QQ 时不要在这里排查 Pan 编排报告。

---

## 6. Manage、managedBy 与父子关系

假设 SMA `ses_parent` 管理 Worker Session `ses_child`：

```text
ses_parent.managed   = ["ses_child"]
ses_child.managedBy  = "ses_parent"
```

`managed` 是“我管理谁”，`managedBy` 是“谁管理我”。前端按这个关系显示树；一个 Session 同时只有一个 `managedBy`，因此一个已被别人管理的 Session 不能再被第二个 manager 认领。关系可以形成多层父子链。

在 MCP 层，调用者身份来自 Pan adapter 注入的 `PAN_AGENT_SESSION_ID`。普通 MCP Session 默认三个 `pan_access` 都是 false；首次 `agent_assign`、`agent_send`、`agent_spawn` 或 `report_subscribe` 会尝试把目标认领到调用者名下，但仍受权限和“目标是否已被其他 manager 管理”限制。Dashboard 的 Manage 操作由后端直接执行，UI 本身不受 `restrictToManaged` 的 MCP 隔离限制。

建立关系：UI 打开 manager 的 Manage，点击目标行 **Manage**；MCP 调用 `session_claim(session_id="ses_child")`。解除整个关系：点击 **Managed** 或调用 `session_unclaim`，会清除 `managed`/`managedBy` 并连带取消报告订阅，但不会删除 Session。只停止报告：点击 **Subscribed** 或调用 `report_unsubscribe`，会保留 managed 关系。

常见误解：Manage 不等于启动 Worker；Subscribe 不等于拥有管理权（但首次 MCP `report_subscribe` 对无主目标会自动 claim）；Kill 不等于删除 Session；删除子 Session 也不等于只解除关系。

---

## 7. Subscribe、报告和 queue_pending

Meta-Agent 先对目标 Session 调 `report_subscribe`，再调 `agent_assign`。目标 Worker 进入 `done` 或 `error` 时，Pan 把报告追加到 manager 的持久化 `queue_pending`：

```json
{
  "status": "done",
  "result": "任务输出",
  "sessionId": "ses_child",
  "taskId": "task-001",
  "workerId": "worker-2"
}
```

`report_signal` 只是唤醒信号，报告内容以落盘队列为准；多份报告可能合并成一次唤醒消息，服务重启或短暂断线后队列仍可恢复。收到报告后，再用 `session_get` 读取完整历史/`lastResult` 做验收。

在 manager Session 的 **Manage → Manages** 中找到目标行，点击 **Subscribe**；成功后显示 **Subscribed**。取消只停止报告，不解除 Managed。点击 **Manage** 会同时建立管理关系并自动订阅；点击 **Managed** 解除管理并自动退订。

[图片占位：pan-user-04-subscribe-report.png]
说明：截图 Manage → Manages 中某个子 Session 同时显示 Managed 和 Subscribed，并标注两者可独立切换。建议插入位置：本节 UI 操作之后。

普通 `agent_send` 只是向目标 Agent 发消息；没有订阅时，仍可读取目标 Session 的结果，但不会按 Meta-Agent 的 `queue_pending` 报告链自动唤醒。WebSocket 的 `worker.result` 是外部协调/排障通道；Meta-Agent 的正常路径是 `report_subscribe → queue_pending`，不要用 WS 盯梢替代它。

如果 `report_subscribe` 返回 404，通常是运行中的服务端版本没有该路由；如果返回 manager 不存在，优先检查三对齐。可以暂时用 `session_get` 查看 `lastResult.status`（`queued → running → done/error`）作为兜底。

---

## 8. pan_access 权限

`pan_access` 是 Session 的 MCP 能力位。模板和 API 使用 camelCase 返回/UI 展示，skill 和配置说明使用 snake_case：

| 配置键 | 含义 | 边界 |
|---|---|---|
| `restrictToManaged` / `restrict_to_managed` | true 时，MCP 只能操作调用者自己、自己 `managed` 列表中的 Session | 不能借 MCP 枚举或操作无关 Session；Dashboard 直接管理不受此开关限制 |
| `canClaimUnmanaged` / `can_claim_unmanaged` | 允许认领当前没有 manager 的 Session | 不能抢已经被其他 manager 管理的 Session |
| `autoClaimCreated` / `auto_claim_created` | 当前 Agent 创建的新 Session 自动加入其 `managed` | 只影响创建后的自动归属；不等于可认领所有旧 Session |

普通 Session 默认全 false。`SMA(NoAdapter)` 和 `SMA(cbc)` 当前默认是 `restrictToManaged=false`、`canClaimUnmanaged=true`、`autoClaimCreated=true`。越权调用通常返回结构化 `permission_denied`/错误结果；无身份时，claim/report/manager-chain 类工具返回 `missing_identity`。

在 UI 的 Manage → Pan Access 修改后，保存的是该 Session 的 MCP 权限。权限不是网络安全鉴权：HTTP API/前端当前是高权限路径，不能因为 MCP 隔离存在就把 Pan 服务暴露到公网。

[图片占位：pan-user-05-pan-access.png]
说明：截图 Pan Access 三个开关及其 snake_case 提示文字，展示 SMA 默认值或普通 Session 默认值。建议插入位置：本节表格之后。

---

## 9. 四种派发工具如何选择

当前代码中的 `agent_*` 是推荐的一等工具，`worker_*` 是兼容旧名：

| 工具 | 做什么 | 是否打断 | 何时用 |
|---|---|---|---|
| `agent_assign(session_id, text, task_id?)` | 异步派发新任务，立即返回 `queued`；无活 Worker 自动 spawn | 不以重启打断 | 新任务、并行 fan-out；重试时复用 `task_id` 防重复 |
| `agent_send(session_id, text)` | 发多轮协作消息，进入持久队列，等目标空闲处理 | 不打断 | 补充信息、追问、后续建议 |
| `agent_send_force(session_id, text)` | 对活 Worker 执行 restart + send | 会打断 | 紧急约束、方向变更、Worker 卡住；无活时退化为入队 |
| `agent_notify(target_session_id, text)` | 把“事后通知”持久化投递到自己或自己管理的 Agent | 不启动普通任务序列；目标无活 Worker 时会唤醒/自动 spawn | 脱离当前 Agent/Worker 生命周期的后台命令、长时测试、编译、外部脚本完成后的回报 |

`agent_notify` 不要和 QQ 的 `/api/qq/notify` 混淆：后者是 QQ 插件向 Pan Core 上报 QQ inbox 更新的内部 HTTP 路由。`agent_notify` 是可调用的通用 MCP 通知工具，但不是普通任务派发的替代品。适合这样使用：启动一个本身遵守权限、审批和安全规则的后台命令（例如 `nohup`、长时间测试、编译或外部脚本），命令结束后由仍可运行的脚本/Agent 调用：

```text
agent_notify(
  target_session_id="ses_parent",
  text="后台测试已完成：pytest 结果为 128 passed，日志已写入 artifacts/test.log。"
)
```

通知会写入目标 Session 的持久化 `queue_pending`；即使原 Worker 已退出，目标无活 Worker 时也会自动唤醒/生成 Worker。它只负责可靠回报，不替后台命令授予额外权限，也不绕过 managed 隔离。普通新任务继续使用 `agent_assign`；向已有 Agent 排队补充消息使用 `agent_send`；需要立即打断并重启后送达使用 `agent_send_force`。要接收子任务完成/错误报告，则使用 `report_subscribe`。

派发是异步编排：`queued` 只表示任务已入队/接受，不表示完成。SMA 应等待报告唤醒，再 `session_get`、验收、必要时补派；不要因没马上看到结果就重复派同一任务。新 Session 的第一条任务应写清背景、目标、文件、边界和验收标准；已有上下文时可简短追问。

---

## 10. Worker 生命周期与 watchdog

1. `session_create` 只创建持久 Session，不启动 Worker。
2. `agent_spawn` 或 UI **Start** 创建一个 CLI Worker；同一 Session 同时只有一个 Worker。
3. `agent_assign`/发送消息会在需要时自动 spawn；无活 Worker 的普通发送会保存到持久队列。
4. Worker 可能是 `queued`、`running`、`idle`、`held`、`error`、`zombie` 等状态；没有进程时 `workerStatus` 为 `null`。
5. watchdog 默认约每 30 秒检查：queued 长时间无输出、stream 任务运行过久、idle 太久都会被回收；`held`（Takeover）和 `zombie` 会跳过普通回收。
6. Worker 被回收只影响进程，不删除 Session、history 或通常的 `cliSessionId`。重新 spawn 会尝试恢复上下文。

无活 Worker 时，优先再次 `agent_assign` 或 UI **Start**；需要显式恢复时调用 `agent_spawn(session_id)`。只有卡死、需要中止进程或配置/MCP 改动要求重启时才用 `agent_kill`/Restart。`agent_kill` 对无活 Worker 是安全 no-op，不会删除 Session。

如果 `queue_pending` 非空但没有活 Worker，全局 watchdog 会尝试自动补员；持续失败请看 `data/logs/pan.log`。异常进程退出会产生 `zombie`/error 报告，主管应把它当作失败处理并决定修复、重试或人工接管。

---

## 11. worktree 并行开发与交付

并行开发最安全的安排是“一个子任务、一个独立 git worktree/分支、一个 Session”。给每个 Session 的 `workdir` 传绝对路径，避免依赖默认目录；若多个 Worker 必须共享同一目录，先确认不会同时改同一文件。

给子 Agent 的任务应明确：只在指定 worktree 修改、不要操作别的 worktree、完成后运行测试并提交 commit、不要 push。SMA 收到报告后检查 `git status`、`git diff --check`、测试结果和 commit，再决定如何合并。合并/冲突解决由明确的一个工作目录负责；不要让多个 Worker 同时在同一分支执行 merge。

```text
只在当前指定 worktree 修改。完成后运行相关测试和 git diff --check，提交一个清晰的 commit；不要 push。报告改动文件、验证结果和 commit hash。
```

Pan 的 Session 删除会 kill Worker、删除 Session 元数据并清理会话级 MCP 配置，但不会替你删除普通 workdir 磁盘目录；重要产物先确认已提交或复制到目标目录。

---

## 12. MCP、模板和端口对齐排障

### 12.1 MCP 接线自检

在 Session 详情或 Manage 面板确认 `pan` 已启用；重启 Worker 后让 Agent 调 `pan_handbook()`。Pan adapter 自动生成的配置会包含 `pan` server 和 `PAN_AGENT_SESSION_ID`。在 `--mcp-config` 自动注入路径下，工具通常是 direct connected；项目级 `.mcp.json` 发现路径才可能需要 ToolSearch/DeferExecuteTool。

### 12.2 常见症状

| 症状 | 先查什么 | 处理 |
|---|---|---|
| 新建后不能运行 | Dashboard CLI status / `GET /api/cli/status` | 安装对应 CLI，确保 Pan 启动环境 PATH 可见后重启 Pan |
| `Worker process dead` | `workerStatus`、`lastResult`、日志 | Start 或 `agent_spawn`；不要删除 Session |
| 发消息没回 | 是否 `queued`、是否 `workerStatus=null`、队列是否有积压 | 再次 assign/Start；查 `data/logs/pan.log` |
| `report_subscribe` 缺少 manager | 是否由 Pan adapter 运行、是否有 `PAN_AGENT_SESSION_ID` | 使用 Pan 管理的 Session；独立 MCP 只能做无身份操作 |
| `report_subscribe` 404 | 服务端是否含 `/api/report-subscribe` | 对齐 MCP 与服务版本；临时用 `session_get` 读 `lastResult` |
| 订阅成功但无报告 | manager 与目标是否同端口/实例，是否实际 assign | 对齐 8768/8767、`PAN_API_URL` 和身份 |
| MCP 改了但 Worker 不生效 | `requireRestart` 或模板锁 | Restart/kill 后 spawn；`always` 至少保留一个 MCP |
| 列表太大 | 是否全量 `session_list()` | 优先 `session_list(summary=true)`，再定向 `session_get(limit=15)` |

---

## 13. 安全、清理与常见问题

- 默认 API 无鉴权并绑定 `127.0.0.1`。不要把 `PAN_HOST` 改为公网地址后再把它当作安全服务；Remote/隧道会扩大暴露面。
- 不要删除用户仍在使用的 Session；删除不是解除管理的替代品。一次性子 Session 确认交付完成后，才用 `session_batch_delete` 收尾。
- 不要按 `ses_*` 全量删除。先看 `session_list(summary=true)`、名称、`managedBy` 和日志；测试夹具或旧队列可能仍有价值。
- `session_unclaim` 只解除关系，不删除会话；`report_unsubscribe` 只停止报告。
- 模型、权限、MCP 服务器变化可能在 Worker 空闲时自动重启，运行中则等任务结束或手动 Restart；不要误以为热改立即改变正在执行的 CLI。
- API body 使用 camelCase（如 `sessionId`、`panAccess`），MCP 参数使用 snake_case（如 `session_id`）；创建返回字段是 `id`，后续请求使用这个值。

### 最小 MCP 编排示例

```text
1. session_create(name="demo-worker", adapter="cbc", model="deepseek-v4-flash")
2. 记下返回的 id，例如 ses_1234...
3. report_subscribe(session_id="ses_1234...")
4. agent_assign(session_id="ses_1234...", text="计算 17*23，并只返回结果和一句验证说明。", task_id="demo-17x23")
5. 等 queue_pending 中的 done/error 报告
6. session_get(session_id="ses_1234...", limit=15)
```

如果只是 UI 用户，不需要手工调用这些工具；创建 SMA 后把目标告诉 SMA 即可。

---

## 14. 开发者速查

| 类别 | 常用 MCP 工具 |
|---|---|
| 会话 | `session_create`、`session_get`、`session_list(summary=true)`、`session_managed`、`session_delete`、`session_batch_delete` |
| 关系 | `session_claim`、`session_unclaim`、`manager_chain` |
| Worker | `agent_spawn`、`agent_kill`、`agent_list` |
| 派发 | `agent_assign`、`agent_send`、`agent_send_force` |
| 报告 | `report_subscribe`、`report_unsubscribe`、`queue_pending` |
| 设置 | `session_update`、`pan_handbook` |

`worker_assign`、`worker_send` 等是兼容旧名；`agent_notify` 是当前实现中的一等 MCP 工具。

### HTTP 关键端点

```text
GET  /api/sessions?summary=1
POST /api/sessions
POST /api/spawn
POST /api/assign
POST /api/send
POST /api/claim
POST /api/unclaim
POST /api/report-subscribe
POST /api/report-unsubscribe
POST /api/notify                 # agent_notify 的内部后端路由
GET  /api/session-templates
GET  /api/mcp/servers
GET  /api/cli/status
```

HTTP 请求体使用 camelCase；MCP 层提供权限/身份检查，直接 HTTP/前端路径不要当成同等隔离的安全边界。完整字段与协议见 [`http-api.md`](skills/pan/references/http-api.md)、[`ws-protocol.md`](skills/pan/references/ws-protocol.md)，编排规则见 [`SKILL.md`](skills/pan/SKILL.md)。

## 关联文档

- [`README.md`](../README.md)：项目概览和安装入口
- [`docs/skills/pan/SKILL.md`](skills/pan/SKILL.md)：给 Meta-Agent 的冷启动 skill
- [`docs/pan-user-manual-images.txt`](pan-user-manual-images.txt)：本文图片截图清单
