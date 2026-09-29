# Pan 用户手册

> 面向 Pan 使用者：介绍安装、Dashboard、Session 与工作区管理、MA 编排、Jobs 和生命周期恢复。界面名称以当前 React Dashboard 为准。

**[English](./USER_MANUAL.en.md) · 中文**

**目录：** [Pan 概念](#overview) · [安装与启动](#install) · [创建 Session](#first-session) · [Session 与 Workspace](#workspaces) · [管理关系与访问](#relationships) · [MA 与报告](#orchestration) · [Jobs](#jobs) · [Worker 与恢复](#lifecycle) · [QQ / SnowLuma](#qq) · [集成、排障与安全](#integrations)

<a id="overview"></a>
## 1. Pan 的基本概念

Pan 把 CLI Agent 的长期身份和临时进程分开管理。**Session** 保存历史、adapter、模型、工作目录、管理关系和队列；**Worker** 是实际运行 CLI 的进程，可在 Session 保留时停止并重新启动。

| 名称 | 含义 |
|---|---|
| Session | 持久会话身份；也承载 MA 或 TA 角色 |
| Worker | 运行某个 Session 的临时 CLI 进程；停止 Worker 不会删除 Session |
| Adapter | cbc、kimi、opencode、claude 或 codex 等 CLI 的连接适配器；可用性取决于本机安装与 PATH |
| MA / TA | MA（meta-agent）负责拆解、派发与验收；TA（task-agent）承接具体工作。二者都是 Session 的职责角色 |
| Workspace / workdir | Workspace 用来整理 Sessions 与共享目录；`workdir` 是该 Session 的实际工作目录，两者用途不同 |

日常单任务可直接使用普通 Session。需要把任务分给多个 Session、统一收取报告时，可使用 SMA 模板。

<a id="install"></a>
## 2. 安装、启动与端口

### 需要准备

- Python 3.10 或更新版本。
- Node.js 20 或更新版本与 pnpm 9.7，用于构建 React Dashboard。
- 至少一个已安装且能从 Pan 进程 PATH 找到的 CLI：`cbc`、`kimi`、`opencode`、`claude` 或 `codex`。缺少个别 CLI 不会阻止 Pan 主服务启动，但对应 Adapter 无法启动 Worker。

### 安装并启动

在仓库根目录运行。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r minimal-requirements.txt
Copy-Item config.example.json config.json
Set-Location packages/web
pnpm install
pnpm build
Set-Location ../..
python main.py
```

macOS/Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r minimal-requirements.txt
cp config.example.json config.json
cd packages/web && pnpm install && pnpm build && cd ../..
python main.py
```

访问 <http://127.0.0.1:8768>。也可用项目启动脚本：Windows `scripts\start_pan.bat`，macOS/Linux `bash scripts/start.sh`；对应的正常关闭脚本为 `scripts\stop.bat` 和 `bash scripts/stop.sh`。直接运行 `python main.py` 时，Ctrl+C 会结束前台进程。

`minimal-requirements.txt` 安装 Pan Core/API/MCP 依赖，不包含 QQ bridge 或 Memory 的机器学习依赖。配置示例的 `qq.enabled` 默认是 `true`；不使用 QQ 时，可在 `config.json` 将它设为 `false`，跳过 QQ 子进程。使用 QQ 时，需在 `qq.python` 或 `PAN_QQ_PYTHON` 指定的解释器中安装 `packages/qq/requirements.txt`，并配置一个网关。详见[QQ 与 SnowLuma 说明](QQ_PLUGIN_SNOWLUMA.md)。Memory 的可选依赖在 `memory-requirements.txt`。

| 设置 | 当前默认值 / 作用 |
|---|---|
| Pan 端口 | `config.json` 的 `port` 默认 `8768`；`PAN_PORT` 可覆盖 |
| 监听地址 | 默认 `127.0.0.1`；`PAN_HOST` 可覆盖 |
| `PAN_API_URL` | 独立 MCP server 连接 Pan 时使用的地址；使用非默认端口时应同步调整 |
| Remote | `config.example.json` 中 `remote.enabled` 默认 `false` |

MCP 身份相关工具还需要 `PAN_AGENT_SESSION_ID` 属于该 Pan 实例。对 `report_subscribe`，MCP 地址、`PAN_API_URL` 与 Session 所属端口必须指向同一实例。

<a id="first-session"></a>
## 3. 创建并使用第一个 Session

1. 打开 Dashboard。侧栏的 **New** 会用默认值快速创建 Session；点击它旁边的设置按钮可打开完整创建表单。**Import** 用于浏览并导入受支持 CLI 的历史会话。
2. 在完整表单中设置名称、Adapter 和可选的 Session Template、模型、权限模式与 workdir。Adapter 只会在对应 CLI 可用时正常启动。
3. 选中 Session，点击顶栏 **Start**。也可以直接发送消息；没有活 Worker 时，Pan 会尝试启动 Worker。
4. 在 Chat 输入任务。按 Enter 发送，Shift+Enter 换行。Worker 忙碌时追加的消息进入发送队列，等当前工作让出执行后继续处理。
5. 在 **Editor** 查看 Session 的 workdir 文件。顶栏也提供 Worker 的 **Restart、Interrupt、Takeover、Kill** 等控制；这些操作针对 Worker，不等于重启或退出 Pan 主服务。

展开 Chat 下方的 **服务端队列**，每条排队消息最右侧的锁按钮可单独锁定或解锁；**Lock all / Unlock all** 一次处理当前所有仍为 `queued` 的消息。锁定不会改变列表或落盘队列顺序；Worker 跳过锁定项，按原顺序选下一条可发送消息。解锁后，该项从原位置重新参与选择。已经进入 `reserved` 或 `writing` 的消息无法撤回或锁定。

点击 **new locked msg** 进入当前 Session 的带锁发送模式，也可点击 **cancel locked msg** 退出该模式。Send 按钮变为红边的 **put in queue**；点击后，正文和附件会作为一条已锁定的用户消息原子入队。入队成功后恢复普通 Send；失败时保留草稿和带锁模式以便重试。带锁消息即使在 Worker 空闲时也只先落盘，不会单独启动 Worker。

**Pause reports / Resume reports** 是同一锁机制的报告自动规则，按 Session 持久化。开启时锁定当前 `queued` 的子 Agent 完成、错误和异常退出报告，并自动锁定以后新入队的此类报告；不会锁定系统通知或 QQ / 微信提醒。即使规则仍开启，也可以单独解锁报告或使用 **Unlock all** 放行已有消息；以后新报告仍会自动锁定。关闭规则只解除报告的自动锁，保留手动锁。

新建的默认 workdir 位于 Pan 服务端。若并行修改同一仓库，应为每个 Session 指定不同 Git worktree；Pan 不会自动创建 worktree。

<a id="workspaces"></a>
## 4. Session 列表与 Workspace

### 使用侧栏管理列表

- 搜索框筛选 Session；可切换按最近活动、名称或自定义顺序排列，也可按 workdir 或 manager 关系分组。
- 右键 Session 可打开 **Pin / Unpin、Rename、Branch、Manage、msgBridge、Details、Select、Delete** 等操作；与导入历史关联的 Session 还可使用 **Reimport**。
- 置顶 Session 显示在当前分组的未置顶 Session 之前。启用拖拽后，可调整自定义顺序或在同一分组内调整置顶项顺序；拖拽目标不同，界面会显示对应提示。
- **Select** 多选模式支持批量移动到 Workspace 和批量删除。删除有子 Session 的管理树时，先阅读确认对话框再选择是否级联。

### 用 Workspace 整理 Session

桌面 Dashboard 左侧的 Workspace rail 列出 **全部** 和命名 Workspace。可新建、重命名、排序、切换或删除 Workspace；**全部**只是列表范围，不会给 Session 写入 Workspace 归属。使用 **Manage → Workspaces** 可将管理树根 Session 移入 Workspace。受管理的子 Session 沿 manager 链继承根节点的 Workspace。

Workspace 不是 Git 分支或目录移动操作。Session 的 `workdir` 决定 Agent 工作位置；Workspace 只保存分组与目录引用。**Editor → Directories** 可为当前 Workspace 添加共享目录，或临时添加额外目录；添加/移除引用不会在磁盘上创建、移动或删除目录。

<a id="relationships"></a>
## 5. 管理关系与访问控制

**Manage** 面板可以查看和编辑 Session 的管理树、Workspace、Pan Access 和 MCP server。若 `ses_parent` 管理 `ses_child`，前者的 managed 列表包含后者，子 Session 的 `managedBy` 指向父级。同一个 Session 同时只能有一个 manager。

- **Manage** 建立管理关系；成功时也会为该子 Session 订阅完成报告。**Managed** 解除关系并退订报告，但不会删除子 Session。
- **Subscribe / Subscribed** 单独控制完成报告订阅。退订后关系仍保留。
- `session_readonly` 可由当前 manager 暂时阻止子 Session 接收其他 Session 发来的任务、消息和通知；它不是文件系统只读，也不是 HTTP 鉴权。
- **Pan Access** 的 `restrictToManaged`、`canClaimUnmanaged`、`autoClaimCreated` 控制 MCP 调用可操作的 Session 范围。它们不限制 Dashboard 的直接管理操作。

<a id="orchestration"></a>
## 6. 创建 MA 并收取任务报告

新用户可在完整创建表单中选择：

- `SMA(NoAdapter)`：加载 SMA 编排模板，创建时仍需选择 CLI Adapter。
- `SMA(cbc)`：使用同一 SMA 模板并固定使用 cbc。

当前两个模板都配置 `pan`、`pan-qq` 和 `pan-wechat` MCP server。模板会给出 MA 编排指引；工具是否能连通仍取决于相应 CLI 与可选通道依赖。

典型流程是先订阅子 Session 的报告，再派发任务：

```text
report_subscribe → agent_assign → Worker 完成或报错 → manager queue_pending → 验收
```

`agent_assign` 用于新任务，返回 `queued` 表示已接受，不表示已经完成。`agent_send` 将补充消息排队且不打断当前任务；`agent_send_force` 用于需要重启并强制送达的紧急消息；`agent_notify` 用于把后台命令或外部任务的事后结果持久通知给目标 Session。完成报告写入 manager 的 `queue_pending`，即使短暂掉线也可在之后读取。

用 Dashboard 时，在 manager 的 **Manage → Relationship → Manages** 中为目标点击 **Subscribe**。这和右键 **msgBridge** 中的 QQ 收件箱订阅不是同一功能。

已有 Agent CLI 也可以启用 `pan` MCP，并在该 Pan Session 的 **Manage → MCP and Plugins** 选择 server。修改 MCP 选择后，重启 Worker 使其在 CLI 中生效。独立运行、未由 Pan Session 注入身份的 MCP server 无法调用依赖 Session 身份的管理和报告工具。

<a id="jobs"></a>
## 7. Jobs 与定时任务

侧栏 **Jobs** 打开任务中心；可按状态或类型筛选、查看详情与运行记录，也可创建、暂停/恢复或删除任务。当前用户表单提供以下类型：

| 类型 | 用途 |
|---|---|
| **定时任务** | 按计划向 Session 派发任务、发送消息，或执行已配置的命令 |
| **Session 消息** | 在选定时间或重复计划向一个 Session 发送文本 |
| **Session 群发** | 按计划向多个 Session 发送同一文本 |
| **后台进程** | 立即在运行 Pan 的主机启动一组 argv 命令；它不是某个 Session 的 Worker，且不会通过 shell 解析 |

计划可使用一次性触发、重复间隔、日历/cron 计划等表单选项；表单会预览接下来的触发时间。定时任务可在详情中运行一次、暂停或恢复；运行记录和错误信息可从详情查看。目标 Session 被删除或无法投递时，任务可能显示不可投递状态，需要在详情中处理目标或任务。

Jobs 页面中的 **Settings** 可配置已完成、失败、超时、取消任务及其日志文件的保留期限。新配置默认不自动清理这些项目；只有明确启用并填写天数后才会按策略清理。

命令类 Job 在 Pan 所在主机运行，使用服务进程可访问的路径和权限。提交命令前确认执行文件与工作目录；后台进程使用 argv 而不是 shell 字符串。

<a id="lifecycle"></a>
## 8. Worker 生命周期、退出与启动恢复

**Start / Restart / Interrupt / Takeover / Kill** 管理当前 Session 的 Worker。Worker 停止或被 watchdog 回收时，Session 和历史仍保留；可再次 Start。Session 的永久删除是另一个操作。

主服务的 **Restart** 和 **Exit** 位于 App Settings → **General**，与顶栏的 Worker **Restart** 不同。在受支持的启动方式下，设置页提供主服务重启；若该按钮显示不可用，请通过启动 Pan 时使用的同一入口重启。当前默认的 **Restart and Exit Session policy** 与 **Startup preference** 都是 **Ask every time**。重启或退出会停止活 Worker；如有 Session 的 legal state 仍为 running，设置允许询问是否标记为 offline 或保留 running 状态。

如果 Pan 启动时发现某些 Session 仍记录为 running，但没有活 Worker，Dashboard 会显示 **Continue previous Sessions?**，列出候选项并要求选择：

1. **Restart these Sessions**：按正常 Session 消息路径向每个候选项发送“继续”。
2. **Keep their legal state as running**：不启动 Worker，保留持久状态。
3. **Update legal state to current Worker state**：不重启，读取当前 Worker 实际状态并更新持久状态。

可在 App Settings → General 更改后续的启动偏好。若选同步实际状态，Dashboard 仍会在恢复时要求明确确认。意外断电或进程崩溃不等同于一次已确认的 Pan Exit；出现恢复对话框时，先核对候选 Session 再选择。

<a id="qq"></a>
## 9. QQ Bridge 与 SnowLuma

NapCat、LLOneBot 和 SnowLuma 都可作为 OneBot 网关在 **App Settings → Plugin** 中选择。当前随仓库提供的插件清单把 SnowLuma 列为可选项；清单中三个插件的 `autoStart` 都为 `false`，示例配置也未默认选择 SnowLuma。QQ 桥接开关 `qq.enabled` 与插件自动启动是两个不同设置。

Plugin 页面中的 **Select** 会保存网关选择；Pan 提示需要重启主服务才能让 QQ bridge 改连。**Start / Stop** 只管理由 Pan 启动的插件进程；**Start with Pan** 只对当前选中的插件生效。状态 **Running (Pan owned)** 表示 Pan 拥有该进程，**Endpoint in use** 表示端口已响应但进程并非由 Pan 管理，**Not installed** 表示注册路径下找不到安装文件。

SnowLuma 的注册字段、WebSocket 地址、token、客户端前置条件和切换步骤见[QQ 与 SnowLuma 使用说明](QQ_PLUGIN_SNOWLUMA.md)。网关状态显示可达并不证明桌面 QQ 已登录或真实消息收发正常；按该说明进行端到端人工检查。

<a id="integrations"></a>
## 10. 可选集成、排障与安全

### 可选集成

- **Memory**：不属于最小运行依赖；启用前按配置安装 `memory-requirements.txt` 中的依赖。
- **Remote**：`remote.enabled` 默认 `false`。启用 Cloudflare Remote 会建立额外远程访问路径，并扩大服务暴露面。
- **MCP / 编排参考**：参见 [Pan skill](skills/pan/SKILL.md)、[HTTP API reference](skills/pan/references/http-api.md) 与 [WebSocket protocol](skills/pan/references/ws-protocol.md)。这些参考适用于高级集成，不是首次启动所必需。

### 常见排查

| 现象 | 检查方式 |
|---|---|
| Session 无法启动 | 查看 Dashboard 的 CLI 状态，确认对应 CLI 在 Pan 的 PATH 中；检查选中的 Adapter 与权限设置 |
| Worker 没有回复 | 查看 Worker 状态和发送队列；确认消息已排队后不要重复发送同一任务，必要时查看 `data/logs/pan.log` |
| 没有收到 MA 报告 | 确认 manager 已订阅目标，并检查 MCP server、`PAN_API_URL`、`PAN_AGENT_SESSION_ID` 是否属于同一 Pan 实例 |
| Jobs 没有触发 | 查看任务是否暂停、下一次触发时间、时区和任务详情中的错误/投递状态 |
| SnowLuma 显示未安装 | 检查 `data/qq_plugins/manifest.json` 中的绝对 `cwd`、启动命令和 WebSocket 地址 |

### 安全与清理

API 默认无身份验证且绑定 loopback。不要仅通过设置 `PAN_HOST` 或打开远程隧道就把它暴露到不可信网络。

手动删除 Session 会停止其 Worker、删除会话记录和历史，但会保留普通 workdir。另有默认关闭的数据保留策略；若启用 Session 自动清理，Pan 可能删除经验证只属于该 Session 且未被其他 Session 或 Workspace 引用的默认 workdir。重要文件应在清理前提交或备份。

## 相关文档

- [README](../README.md)：项目简介和快速开始。
- [English user manual](./USER_MANUAL.en.md)。
- [QQ 与 SnowLuma 使用说明](./QQ_PLUGIN_SNOWLUMA.md)。
