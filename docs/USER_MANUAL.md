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
| Worker | 运行某个 Session 的临时 CLI 进程；既有 MA Worker 也有 TA Worker。停止 Worker 不会删除 Session |
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

`minimal-requirements.txt` 安装 Pan Core/API/MCP 依赖，不包含 QQ bridge 的依赖。配置示例的 `qq.enabled` 默认是 `true`；不使用 QQ 时，可在 `config.json` 将它设为 `false`，跳过 QQ 子进程。使用 QQ 时，需在 `qq.python` 或 `PAN_QQ_PYTHON` 指定的解释器中安装 `packages/qq/requirements.txt`，并配置一个网关。详见[QQ 与 SnowLuma 说明](QQ_PLUGIN_SNOWLUMA.md)。Memory / Character 当前搁置，无需为日常使用安装其历史依赖。

| 设置 | 当前默认值 / 作用 |
|---|---|
| Pan 端口 | `config.json` 的 `port` 默认 `8768`；`PAN_PORT` 可覆盖 |
| 监听地址 | 默认 `127.0.0.1`；已有 `PAN_HOST` 可绑定外部地址，仅启动时读取 |
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

点击 **new locked msg** 进入当前 Session 的带锁发送模式，也可点击 **cancel locked msg** 退出该模式。Send 按钮变为红边的 **put in queue**；点击后，正文和附件会作为一条已锁定的用户消息原子入队。入队成功后恢复普通 Send；确认拒绝时恢复草稿和带锁模式，结果未确认时先核对再重试。带锁消息即使在 Worker 空闲时也只先落盘，不会单独启动 Worker。

**Pause reports / Resume reports** 是同一锁机制的报告自动规则，按 Session 持久化。开启时锁定当前 `queued` 的子 Agent 完成、错误和异常退出报告，并自动锁定以后新入队的此类报告；不会锁定系统通知或 QQ / 微信提醒。即使规则仍开启，也可以单独解锁报告或使用 **Unlock all** 放行已有消息；以后新报告仍会自动锁定。关闭规则只解除报告的自动锁，保留手动锁。

新建的默认 workdir 位于 Pan 服务端。若并行修改同一仓库，应为每个 Session 指定不同 Git worktree；Pan 不会自动创建 worktree。

**Pause everything / Resume everything** 按 Session 持久暂停/恢复整个队列的后续投递，与单条锁和 Pause reports 独立。暂停时仍可入队；不会中断正在运行的任务，也不能撤回已交接项。支持的 Adapter 在运行中提供 **Steer**，用于追加当前任务的指令；队列条目的 Steer 也遵守锁与全队列暂停。交接结果未确认时不要重发。

合法的 **Send / Steer** 提交会立即清空输入并显示等待转圈。确认拒绝后恢复原草稿；结果未知时保留待确认事务并阻止重复提交。请先核对队列、历史及界面结果，再决定重试；输入清空或入队回执不代表任务完成。

### 替身交接与原始提示

在 Session 菜单选择 **替身交接**，填写必填的交接简报。新会话接管关系与设置，旧会话归档，新 CLI 身份不继承旧对话历史。默认继承源会话的原始提示；启用 **覆写原始提示** 后使用下方文本，留空会清空原始提示。关闭开关后即使文本仍在也不生效。新系统提示由本次简报和选定的原始提示组成，不继承旧简报，也不修改源会话提示。失败保留输入；关闭重开或切换 Session 会重置表单。

<a id="workspaces"></a>
## 4. Session 列表与 Workspace

### 使用侧栏管理列表

- 搜索框筛选 Session；可切换按最近活动、名称或自定义顺序排列，也可按 workdir 或 manager 关系分组。
- 右键 Session 可打开 **Rename、Branch、Manage、msgBridge、Details、Select、Delete** 等操作；与导入历史关联的 Session 还可使用 **Reimport**。
- 置顶 Session 显示在当前分组的未置顶 Session 之前。启用拖拽后，可调整自定义顺序或在同一分组内调整置顶项顺序；拖拽目标不同，界面会显示对应提示。
- **Select** 多选模式支持批量移动到 Workspace 和批量删除。删除有子 Session 的管理树时，先阅读确认对话框再选择是否级联。

### 用 Workspace 整理 Session

桌面 Dashboard 左侧的 Workspace rail 列出 **全部** 和命名 Workspace。可新建、重命名、排序、切换或删除 Workspace；**全部**只是列表范围，不会给 Session 写入 Workspace 归属。使用 **Manage → Workspaces** 可将管理树根 Session 移入 Workspace。受管理的子 Session 沿 manager 链继承根节点的 Workspace。

Workspace 不是 Git 分支或目录移动操作。Session 的 `workdir` 决定 Agent 工作位置；Workspace 只保存分组与目录引用。**Editor → Directories** 可为当前 Workspace 添加共享目录，或临时添加额外目录；添加/移除引用不会在磁盘上创建、移动或删除目录。

### 搜索、未读与批量操作

列表搜索匹配 Session 名称、ID、CLI 历史 ID、workdir 或 Adapter，仅在 **Workspace Bar** 展开时，共享搜索框也独立筛选 Workspace 名称；收起时保持原 Session 搜索。筛选项包括 **有受管理子 Session、Is MetaAgent、Running state、Illegal state、Has unread、Has queued messages**；Session 必须满足全部已选条件。Workspace 的每个条件只需有一个成员满足，不必由同一成员满足。列表搜索与 Chat 历史搜索不同：**Search all Sessions** 搜索跨 Session 消息历史，当前 Session 搜索只查本会话；可按角色筛选并导航命中，结果过期时重新搜索。

黄色计数表示未读完成结果；旁边队列计数为蓝色，待发项全部锁定时为红色。打开队列不会清除队列计数。Workspace 菜单的 **Read all** 标记其实际成员已读，包括被搜索/筛选隐藏的成员；操作后新到的完成结果仍会保留未读。多选 **Actions** 提供 **Workspace、Read all、群发、Bell on、Bell off**，针对所选目标执行。群发创建 Job，请核对逐目标结果及部分失败后再重试。

Workspace 范围还包括 **全部** 与 **无工作区**。一个 Session 最多属于一个 Workspace，受管理子 Session 继承根节点归属。新建/导入默认归属当前命名 Workspace。跨 Workspace 移动受管理子树可能解除其 manager 关系，按设置显示确认。

Session 行上的 **Pin** 快捷按钮用于置顶/取消置顶，右键菜单不再提供 Pin。**Codex Reimport** 发现来源不匹配时会显示摘要供核对。**取消**保留当前历史；**确认强行覆盖**会以原生历史替换，无法匹配的 Pan 消息及任务、来源、附件信息可能丢失。确认期间历史变化时，需**重新检查**并再次核对。

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

`agent_assign` 用于新任务，返回 `queued` 表示已接受，不表示已经完成。`agent_send_to_queue` 用于明确留待当前任务结束后处理的独立后续事项；当前任务的补充、澄清、纠正和约束更新默认使用 `agent_send`，由 Pan 按能力选择送达方式。accepted 仅表示 CLI 接受，unknown 不得自动重发；`agent_notify` 用于把后台命令或外部任务的事后结果持久通知给目标 Session。完成报告写入 manager 的 `queue_pending`，即使短暂掉线也可在之后读取。

用 Dashboard 时，在 manager 的 **Manage → Relationship → Manages** 中为目标点击 **Subscribe**。这和右键 **msgBridge** 中的 QQ 收件箱订阅不是同一功能。

已有 Agent CLI 也可以启用 `pan` MCP，并在该 Pan Session 的 **Manage → MCP and Plugins** 选择 server。修改 MCP 选择后，重启 Worker 使其在 CLI 中生效。独立运行、未由 Pan Session 注入身份的 MCP server 无法调用依赖 Session 身份的管理和报告工具。

**Stop reports / Start reports** 控制向 manager 自动汇报，不解除管理关系；它不同于接收队列中锁住报告的 **Pause reports**。已完成任务的最后 assistant 消息块可出现 **汇报** 按钮，自动汇报关闭时也可手动发送。仅保留在可证明终态窗口内的任务适用，不能对任意消息或全部历史任务汇报；较新任务仍在运行不影响较早且仍符合条件的结果。同一任务运行重复汇报会去重。

高级 Agent 可通过 self queue MCP 在工作中查看和接收自己的可投递队列消息。查看不消费；消费可能打断当前任务。它不能选择其他 Session、绕过锁或 Pause everything，也不能将未知回执当成功。接线细节见[编排手册](skills/pan/SKILL.md)。

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

Job 行菜单的 **Pin / Unpin** 可让重要 Job 排在未置顶项前。置顶只影响展示，不改变调度或执行；显示 **Pin unavailable** 时暂不能修改置顶元数据。

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

- **Memory / Character**：当前搁置；历史依赖和设计说明不代表已有可用的用户流程。
- **Remote**：`remote.enabled` 默认 `false`。启用 Cloudflare Remote 会建立额外远程访问路径，并扩大服务暴露面。
- **MCP / 编排参考**：参见 [Pan skill](skills/pan/SKILL.md)、[HTTP API reference](skills/pan/references/http-api.md) 与 [WebSocket protocol](skills/pan/references/ws-protocol.md)。这些参考适用于高级集成，不是首次启动所必需。

### 通知与未读记录

在 **App Settings → Notification → Completion bell defaults** 选择 System、Browser、QQ Report 或 QQ Subscribe，再点 **Save completion defaults**。QQ 选项必须选择至少一个默认联系人，可按名称或号码搜索。默认值在下次点亮未亮的 Session Bell 时应用，不替换已有连接。Browser 还需浏览器通知权限；启用 Bell 时 QQ bot 和所选联系人须可用。QQ Report 发送结果，QQ Subscribe 接收 QQ 收件箱提醒。

**Unread recording** 默认 All sessions，可选 No sessions 或 Bell on/off、Pinned 等类别；Bell on 与 Bell off 互斥，Pinned 可与其中一个按并集组合；All sessions 与 No sessions 互斥，且都不能与类别同时选择。排除的 Session 保持零未读，重新纳入只统计新完成结果，不恢复旧未读。Session 菜单可单独启用记录；保存全局规则或 **Reapply settings** 会清除这些覆盖。

Pan 页面存活时，浏览器完成通知点击会定位对应 Session；浏览器完全关闭后页面通知不可用。Windows System 完成通知使用真实 Windows toast（WinRT）：通知会被录入 Windows 通知中心（Action Center）持久保留，点击通知上的「打开会话」按钮由系统 http 处理器（默认浏览器）打开对应 Session 深链——发送进程退出后按钮仍有效（冷启动已完全退出的浏览器尚未实测）。通知正文与结果文本不会进入 URL，只用本机 loopback 深链。同一 Session 的完成通知在通知中心内按目标替换，重复的提醒按内容去重；安装/卸载不注册自定义协议或快捷方式。Browser 与 System 是独立通道，没有“Browser 失败才发 System”选项；已派发通知或亮 Bell 不代表已送达。

Windows 发送器会在当前用户的 `HKCU\Software\Classes\AppUserModelId\Pan.Desktop.Notifications` 注册应用显示名 `Pan`，供系统 Shell 展示应用身份；不修改通知权限、勿扰或全局策略。完成通知的主体与「打开会话」按钮均携带相同的协议深链，由系统默认浏览器处理，不依赖发送 helper 常驻。通知中心延迟点击主体已通过本机人工验证；展开后的按钮与完全退出的浏览器冷启动尚未分别复验。超长标题/结果只在系统通知中显示限长预览，完整结果保留在 Session。通知显示与保留仍受 Windows 和用户设置控制，API 接受不代表已经展示。

### Tailscale 备用访问

在 **App Settings → General → Tailscale 备用访问** 使用已安装、已登录且属于同一 tailnet 的 Tailscale。选择协议/端口，**保存配置**后点**开启当前访问**（或**应用已保存入口**）；只保存不会开启。Pan 保持回环监听，使用 Serve，不开放公网 Funnel；下次启动默认关闭。HTTPS 可能需先在 Tailscale 管理界面启用。正常退出关闭自有映射，强制退出可能保留至下次协调恢复；不会接管外部映射。界面“已开启”不证明另一设备可达。高级限制见[备用访问说明](tailscale-fallback-access.md)。

### 普通局域网访问（LAN）

从电脑本机的 Pan 回环地址打开 **App Settings → General → 普通局域网访问（LAN）**。默认关闭；选择本机 IPv4 与空闲端口（默认 `8780`），保存后点**开启 LAN 访问**，在可信同网设备上访问显示的 URL。保存不启停，start/stop 无需重启 Pan 即时生效；**下次启动 Pan 默认开启 LAN** 控制启动恢复。修改运行中的地址/端口必须先关闭当前入口。`0.0.0.0` 包含所有 IPv4 接口及 VPN，不只限于 LAN。

请从本机 loopback 入口管理。LAN 入口禁止本机身份桥、agent WebSocket、主服务控制、全局设置写入和 managed/readonly 管理；没有全站登录保护，HTTP 未加密。直接设置 `PAN_HOST` 是启动时开放主入口，不具备独立 LAN 入口的管理隔离；保留本机管理时应保持其回环默认值。手机访问、防火墙行为及 live Cloudflare Tunnel / Tailscale 共存尚未实测。详见[LAN 使用与限制](lan-access.md)。

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

手动删除 Session 会停止其 Worker、删除会话记录和历史，但会保留普通 workdir。另有默认关闭的数据保留策略；Session 自动到期同样保留 workdir；Settings → Data 中独立的孤立目录清理遵循自己的 N 天规则，并保护所有 Session 与 Workspace 引用。重要文件应在清理前提交或备份。

### 移动入口与 Codex Terminal TUI

视图入口按三个一组展示：Chat / Editor / Jobs，下一组是 Terminal。移动端在入口栏滑动，桌面可用箭头或入口栏滚轮切组；切组本身不跳转页面。Chat 的 **TUI / Bubble / Raw** 只切换消息展示，不改变 Agent 控制模式。

Codex **Terminal TUI** 是单独的原生控制入口：桌面在顶栏，移动端在 **Session Settings → More**。旧 Session 可能显示升级并等待下一次 idle 边界，请以界面的等待/错误状态为准。它不同于停止 Worker 后打开外部终端的 **Takeover**，也不同于 Terminal 页面独立运行的 shell。

## 11. 常驻终端（Windows）

Dashboard 的 **终端** 页面提供独立常驻 shell，不需要创建或启动模型 Session。
可选的 Workspace/Session ID 仅为关联元数据，不授予权限，也不改变进程寿命。

1. 在“工作目录”填写有效目录（可留空），点击“新建终端”。默认 shell 为
   `cmd.exe /q /d`。当前版本支持 Windows；需要 Python 最小依赖和可执行的 Node。
   首次安装需在 `packages/core/terminal/emulator_sidecar` 执行 `npm ci`，使用仓库
   锁定的 headless/serialize 版本，不安装全局包。
2. 新连接默认只观察；点击“取得输入控制权”后才会输入或调整 PTY 尺寸。同一终端
   同时只有一个控制连接，新连接取得控制权会撤销旧控制权，旧输入不会重放。
3. 关闭页面、断线、刷新或离开终端页面不会终止 shell。“重连”重新读取服务器
   屏幕；`partial` 表示只恢复已确认的部分状态，不等于完整 TUI 保真。发生输出 gap
   时会明确重建显示基线，不自动 reset 正在运行的 shell。
4. Ctrl-C 通过终端输入转交前台程序。本机生产 launcher 已验证 Windows
   `CTRL_C_EVENT` 和中断后 shell 可继续执行，但程序可忽略 Ctrl-C 或自行改变输入
   模式。Ctrl-D 不承诺通用 POSIX EOF。
   Ctrl-V 使用浏览器剪贴板粘贴；程序启用 bracketed paste 时由 xterm 生成对应
   边界，而不是发送 Ctrl-V 控制字节。粘贴多行命令仍可能被程序立即执行，请先
   检查内容。Git/MSYS 的 `less` 等程序若提示终端能力不足，可在该 shell 内设置
   `TERM=xterm-256color`；这不会使 partial 快照自动变成完整 TUI 恢复。
5. 默认终端在 Pan 停止或宿主失联后收尾。若希望跨 Pan 重启保留 shell，先点击
   “持久脱离”。只有 runner 确认不受外层宿主 Job 控制才会成功；环境不允许
   breakaway 时明确拒绝，终端不被替换。成功后新 Pan 核验原 PID/FILETIME 和本地
   凭据，再接回原 shell；旧连接控制 token 失效，重连后须重新取得控制权。
6. “终止终端”会停止整棵自有进程树并清理凭据；未确认完成时保持可重试状态，
   不把“已发停止请求”当作“已清理”。持久脱离不保证跨系统重启、注销或 sidecar
   重启恢复，也不能抵抗用户显式结束进程。
7. shell 自己执行 `exit` 后会自动收尾，尾部输出先显示，再公布实际退出码和
   exited 状态；根进程已退出不等于输出已确认完整。当前面板保留最后显示的屏幕，
   列表自动刷新退出记录，不能重连已退出的进程。历史屏幕不持久化：离开后重新选择
   已退出记录只展示退出说明，不假装恢复旧屏幕。需要保留的结果请自行复制。

终端 REST/WS 只面向可信本机控制面，有同源/Host 防护。`PAN_TERMINAL_ALLOW_REMOTE`
不是远程身份验证；不要用它把 shell 暴露到不可信网络。浏览器不持有 runner token，
本地凭据使用同用户 DPAPI 和 owner-only ACL；不要删除仍在运行终端的 secrets 文件。
机器字段、接口和已验证边界见[终端服务接口](design/PAN_TERMINAL_SERVICE_INTERFACES_20261003.md)
与[durable 验收](design/PAN_TERMINAL_DURABLE_MA_20261004.md)。

## 相关文档

- [README](../README.md)：项目简介和快速开始。
- [English user manual](./USER_MANUAL.en.md)。
- [QQ 与 SnowLuma 使用说明](./QQ_PLUGIN_SNOWLUMA.md)。


### Orphan workdir cleanup (Settings → Data)

Workdir cleanup is independent of Session retention. Deleting or expiring a Session always preserves its workdir. Configure `data_retention.workdirs` with `enabled` (default `false`) and `days` (integer 1–36500; `null` disables cleanup). Existing configurations remain opt-in. Automatic cleanup uses the existing daily retention scheduler.

Set a day count and choose **Browse cleanup candidates** to inspect directory names, paths, latest recursive modification times and entry counts. Refresh, select individual rows or Select all, then Delete selected. Deletion is permanent; per-directory failures remain visible and can be refreshed. This action deletes orphan directories, never Sessions. Manual cleanup uses the displayed day count without requiring automatic cleanup to be enabled or settings to be saved.

**The “no changes for N days” check may be inaccurate. Enable automatic cleanup with caution; manual cleanup is recommended.** Filesystem mtimes can be preserved by copies or changed by background tools; they do not prove that data is disposable.

Only direct plain directories under Pan's default `data/workdirs` root are scanned. The newest mtime across every file and subdirectory must be older than N days. Any Session (including archived or offline Sessions), or Workspace directory referencing the same path, an ancestor or descendant protects it. Links/reparse points, hardlinks, special files, malformed reference records and scan errors prevent deletion. No arbitrary client path is accepted. All checks run again before deletion; preview is not deletion authority. Scan limits (2,000 root entries, 100,000 tree entries, 2,048 directories per tree and a 20-second tree traversal budget per request) fail closed and are reported with skip/error counts. Partial deletion failures can leave a directory partially cleaned; inspect failures before retrying.
