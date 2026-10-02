# Codex 无中断网页原生 TUI 可行性（第一轮探索）

日期：2026-10-03。任务：T-TERMINAL-PTY-20261003 / Codex 无中断 TUI。
工作树：`D:/project/pan-worktrees/terminal-codex-explore-20261003`，
分支 `explore/terminal-codex-20261003`，起点 `35b6fb1abaae9d741b3042470aa32a8c356e431d`。
专属目录：`audit/terminal/codex/`。状态：探索完成，交付可复现探针与证据；**不是**完整终端功能实现。

---

## 0. 结论摘要

**传输层可行**（已实测）：原生 Codex TUI 可以作为一个普通客户端连接到 Pan 正在使用的同一个
app-server。昨天"无法切换"的假设不成立。

**但必须把两条 thread 分开说，不能合并成"无中断"**（已实测）：

| thread 角色 | attach/detach 期间 | TUI 退出后 |
|---|---|---|
| **未被 TUI resume 的对照 thread** | 后端 PID 不变、turn 保持 `inProgress`、Pan 侧连接收到通知数继续增长 | 仍 `inProgress`（观测窗口内 65.7s） |
| **被 TUI resume 的目标 thread** | 后端 PID 不变、turn 保持 `inProgress` | **`interrupted`** |

也就是说：**"原 backend 进程存活"对两种情况都成立；"运行中 turn 不被 attach 打断"目前只对
未被 resume 的对照 thread 成立。** 目标 thread 一旦被原生 TUI `resume`，TUI 退出（有/无
Ctrl-C 皆然）会使其运行中的 turn 变为 `interrupted`。

**中断归因仍是推断，不是已证结论**：同一进程内的对照 thread 存活；而普通 JSON-RPC 客户端
做同样的 `thread/resume` 后断开**不会**造成中断。据此**推断**中断与"原生 TUI 附着生命周期"
相关、且与 Ctrl-C 无关，但具体机制未定位（详见 §3.5）。

**当前 Pan 拓扑接入原生 TUI 需要改传输**（源码 + 传输定义实测）：Pan 用
`[node, codex.js] app-server --stdio`，stdio 是父子进程之间的私有管道，没有可供第二个客户端
连接的监听端点。**若将来要接原生 TUI**，需要改为 `--listen ws://127.0.0.1:PORT` 或
`unix://`；这项改造**不是首版普通持久 PTY / Web 终端的前置条件**（见 §1 范围决定）。

**能力差异不在版本上**（实测）：本机 npm 安装版 `0.159.2` 与本机已有上游构建 `0.160.0`
在 7 条命令路径上的 `--help` 输出**逐字节相同**；`--remote`、`--listen ws://`、
`app-server proxy`、`resume <SESSION_ID>`、`agents`、`remote-control` 在安装版中**均已存在**。
无需升级即可做探针与原型。

---

## 1. 探索问题与判定口径

brief 要求的验收：**保留原 backend、运行中 turn、输入控制权与结构化事件**；
原生 TUI 能 attach/return；不以 resume 新进程充当保留原进程的证明。

本报告把结论分成三档，全文一致：

| 标记 | 含义 |
|---|---|
| **实测** | 由本工作树内探针真实运行产生，证据文件在 `audit/terminal/codex/evidence/` |
| **推断** | 由实测结果或官方文档推导，尚未直接观测 |
| **未验证** | 本轮没有做，或有明确理由不能做；给出原因与下一步实验 |

**本轮范围与首版决定（MA，2026-10-03）**：首版范围以**普通持久 PTY / Web 终端**优先；
**原生 Adapter 无中断 TUI 作为后续探索**。因此本轮**不继续**做中断机制定位、真实模型请求
与审批实验；§4 的 `--stdio` → `--listen ws://` 改造**不作为首版前置**，
仅在未来重启原生 TUI 方向时才需要。本文的负结果按"后续探索的输入"来读，
不构成对首版普通终端方案的阻塞。

---

## 2. 环境、版本与隔离

### 2.1 版本对照（实测）

`audit/terminal/codex/probe_cli_surface_diff.py`（只执行 `--help`，不装、不启停任何服务）：

| | 安装版（Pan 实际解析的入口） | 上游构建（本机已有） |
|---|---|---|
| 路径 | `D:\node_npm\node_global\node_modules\@openai\codex\bin\codex.js` | `~/.codex/packages/app-server-daemon/releases/0.160.0-x86_64-pc-windows-msvc/bin/codex.exe` |
| 版本 | `codex-cli 0.159.2` | `codex-cli 0.160.0` |
| npm 最新 | `0.160.0`（`npm view`） | — |

对照了 `top-level`、`app-server`、`app-server daemon`、`app-server proxy`、`resume`、
`agents`、`remote-control` 七条路径：**全部 IDENTICAL，无任何单侧 flag**。
证据：`evidence/cli-surface-diff-20261003-004837.json`。

> 说明：本机 `~/.codex/packages/app-server-daemon/releases/` 下同时存在 0.158.0 / 0.159.0 /
> 0.159.2 / 0.159.3 / 0.160.0，是 Codex 自带 updater 留下的；0.160.0 只是"本机已有的最新构建"，
> 不等同于官方最新发布版。帮助文本一致**不代表**运行期行为完全一致（见 §5 未验证项）。

### 2.2 机器上既有的、**不属于本探针**的 Codex 资源（实测，只读观察）

探索开始时（2026-10-03 00:2x）观察到，全程未连接、未操作、未发信号：

- **共享托管 daemon**：PID 32480，`app-server --listen unix:// --managed-daemon`，
  二进制 0.160.0，创建于 2026-10-02 20:47:42，控制套接字
  `~/.codex/app-server-control/app-server-control.sock`。
  **本轮结束时 PID 与创建时间完全未变**（见 §7 清理核对）。
- 另有 VS Code 扩展的 codex 进程、`codex-code-mode-host.exe`、以及两个 Pan Worker 的
  codex 进程（创建于 00:12 / 00:17）。探针从未与它们交互。

### 2.3 隔离手段（实测）

每次运行都满足 brief 的"自己的 backend/session/thread + 空闲 loopback 端口"：

- **临时 `CODEX_HOME`**：每次运行新建 `%TEMP%\codex_tui_probe_*`，运行结束整体删除并断言
  已删除。用户真实 `~/.codex` 的 sessions / state DB / daemon 状态**全程未被读写**。
  这一点是刻意的：本机 daemon 跑 0.160.0 而安装版是 0.159.2，让 0.159.2 去开同一批
  sqlite 状态库存在 schema 版本迁移风险，可能影响用户正在跑的 Worker。
- **合成占位凭据**：TUI 有本地登录门。探针用
  `codex login --with-api-key` 往**自己的临时 home** 写入一个自造的、明显不是凭据的占位 key，
  仅用于越过登录界面。**没有读取、复制、打印或改写任何真实凭据**。
- **本地黑洞模型提供方**：`-c model_provider=probe_bh` +
  `model_providers.probe_bh={base_url=http://127.0.0.1:<自有端口>/v1, wire_api="responses"}`，
  指向探针自己起的"只接受连接、永不回包"的 loopback listener。效果是 turn 稳定停在
  `inProgress`，**不产生任何外网请求、无费用、无模型输出**。
- **自有监听端口**：由 `bind(127.0.0.1, 0)` 选出空闲端口，仅绑定回环。
- 进程身份用 **PID + 创建时间** 双重记录（`psutil`）。

---

## 3. 实测结果

### 3.1 原生 TUI 能连上同一个 app-server（实测）

对照组先证明"连上"是真信号：把 TUI 指向一个**没有监听**的回环端口，TUI 会退出并打印

```
Error: failed to connect to remote app server at `ws://127.0.0.1:55870/`:
IO error: 由于目标计算机积极拒绝，无法连接。 (os error 10061)
```

证据：`evidence/control-20261003-005009.json`（`tui_alive_after_12s=false`，
`connect_error_visible=true`）。

主场景下 TUI 在 Windows ConPTY 中正常渲染（alternate screen、鼠标、bracketed paste），
显示 `OpenAI Codex (v0.159.2)`，无连接错误。用**透明 WebSocket 中继**（只记录字节，
TUI 与 app-server 都是真的）抓到了 TUI 真实发出的 JSON-RPC：

```
initialize, initialized, account/read, model/list, configRequirements/read,
collaborationMode/list, hooks/list, config/read, thread/start,
thread/loaded/list, thread/list, thread/read, thread/turns/list, skills/list, plugin/list
```

证据：`evidence/tap-20261003-004937.json`。

**要点**：裸 `codex --remote <url>` 自己去调 `thread/start` 开**新** thread，
**不会**自动挂到 Pan 正在跑的会话上。

### 3.2 显式 attach：`codex --remote <url> resume <thread-id>`（实测）

`codex resume` 自身支持 `--remote`（`codex resume --help` 实测）。把 TUI 起成
`--remote ws://… resume <Pan 的 thread id>` 后，中继抓到 TUI 确实发出了
`thread/resume`，参数里的 `threadId` 与 Pan 侧的 thread id **完全一致**：

```
wanted: 01a0fd85-006c-7251-bded-608e7fc6b1d8
got   : ['01a0fd85-006c-7251-bded-608e7fc6b1d8']
```

同一次运行中：TUI 渲染成功、后端 PID 不变、目标 turn 在 attach 期间仍是 `inProgress`。
**在接了中继的这次运行里**，TUI 发出的 `turn/interrupt` 次数为 **0**（该计数只对
"中继覆盖到的这段连接"有效；`exit-force` 场景未接中继，见 §3.5）。
证据：`evidence/tap-20261003-004937.json`（`attach_tui_resumed_pan_thread`、
`attach_tui_sent_no_interrupt`）。

### 3.3 后端进程与运行中 turn 的连续性（实测）

两次独立运行（`main-20261003-005121.json`、`tap-20261003-004937.json`）结果一致。
**注意下表区分了"对照 thread"与"被 resume 的 thread"**：

| 观测点 | 值 |
|---|---|
| 后端 node PID | 26900（main）/ 3796（tap）；attach 前 = attach 中 = detach 后（**同一 PID**） |
| 进程身份记录 | 每个进程各自记录 `create_time`（FILETIME 换算）并**前后比较**，两次运行均未变 |
| **对照 thread**（未被 TUI resume）的 turn | attach 前 / 中 / 后均 `inProgress`；运行 65.7s 时仍 `inProgress` |
| **被 resume 的目标 thread** 的 turn | attach 期间 `inProgress`；**TUI 退出后 `interrupted`**（§3.5） |
| Pan 侧收到的通知条数 | 11 → 24 → 26（attach 期间继续增长） |
| Pan 侧连接 | 全程未断 |

关于"11 → 24 → 26"的准确含义：**它是 Pan 侧连接收到的 JSON-RPC 通知/响应条数增长，
即"连接仍然活着且仍在收消息"，不是模型流式 token 产出。** 本轮用本地黑洞提供方，
模型请求始终挂起，因此**没有测到任何模型输出，也没有验证真实任务的连续性**（见 §5 未验证项 1）。

**可以下的结论**：attach/detach 期间**后端进程本身**（PID 与身份记录不变）与
**未被 resume 的对照 thread 的运行中 turn**保持存活，Pan 侧连接未被 attach 打断。
**不能下的结论**：被 resume 的目标 thread 上的 turn 也能无中断保持。

### 3.4 第二个客户端的结构化事件订阅（实测，负结果）

**被测量的范围**：安装版 `0.159.2`、`websockets` 传输、客户端 B 走
`thread/resume` 这一条路径。**本轮没有测其他 provider、其他传输（`unix://`）、
其他订阅路径（如先 `thread/unsubscribe` 再接管、或 `experimentalApi` 能力差异），
也没有测 0.160.0。** 下面的结论只在这个范围内成立。

在该范围内：**第二个客户端拿不到该 thread 的实时 turn/item 事件流。**

- 客户端 B `thread/resume` 成功后（返回正常），在**后续**由 A 发起的 turn 上，
  收到的 turn/item 事件数为 **0**。
- 让 B 在 turn 开始**之前**先 `resume`，再由 A 发起 turn：B 仍然收到 **0** 个 turn/item 事件。
- B 收到的全部方法类型只有：
  `deprecationNotice`、`remoteControl/status/changed`、`thread/goal/cleared`、
  `thread/started`、`thread/status/changed`。
- 对照：A 全程正常收到 `turn/started`、`item/started`、`item/completed`、`turn/completed`。

证据：三个 tap 运行中该检查均稳定失败（`late_subscriber_sees_activity_on_resumed_thread`、
`subscriber_before_turn_receives_turn_events`），以及
`second_client_received_methods` 字段。

与官方文档的关系：文档说 `thread/start` 会自动订阅该 thread 的 turn/item 事件、
`thread/unsubscribe` 解除订阅。实测在"已有另一个连接在驱动该 thread"时，
第二个连接只拿到 thread 级通知。**这是文档未覆盖的行为差异，不是"多客户端方案不可能"
的证明** —— 需要区分"这条 resume 路径拿不到"与"所有 provider 多客户端方案都不行"。
原生 TUI 自己也是靠 `thread/read` / `thread/turns/list` / `thread/items/list` **轮询历史**
来补屏，这与"它在这条路径上收不到实时流"一致。

**据此给出的建议（推荐策略，未被证明是唯一可行方案）**：在把 provider 事件当成
多客户端可共享的广播之前，先假定**每个 thread 的实时事件只有一个接收者**，由 Pan 作为
该接收者并自行向 Web 客户端扇出。若后续要依赖 provider 广播，需要先补做
§5 未验证项 9 的实验。

### 3.5 attach 结束后 turn 的命运（实测，关键限制）

这是本轮最重要的负面结论。分离实验（`--scenario exit-ctrl-c` / `exit-force`）：
先建 thread + 运行中 turn，TUI 以 `resume <thread>` attach，确认 `inProgress`，然后退出：

| 退出方式 | attach 期间 turn | 退出后 turn | 后端 PID |
|---|---|---|---|
| 先发 Ctrl-C（ETX）再关闭 PTY | `inProgress` | **`interrupted`** | 不变 |
| **不发任何按键**，直接强关 PTY | `inProgress` | **`interrupted`** | 不变 |

证据：`evidence/exit-ctrl-c-20261003-010604.json`、`evidence/exit-force-20261003-010634.json`。

两组对照（来自**另外的运行**，与上面两个 exit 场景不是同一次实验，因此是旁证而非同批对照）：

- **同一进程内的对照 thread**（从未被 TUI `resume`）：在同一时间窗口结束时仍 `inProgress`
  （65.7s，`main-20261003-005121.json`）。→ 说明"黑洞 turn 自己会超时"不足以解释该中断。
- **普通 JSON-RPC 客户端**做同样的 `thread/resume` 再断开：目标 turn **没有被中断**，
  对照 thread 也没有。证据：`evidence/subscriber-20261003-010508.json`、`subscriber-20261003-010751.json`。

**推断（范围受限）**：在"安装版 0.159.2 + ws 传输 + 该 resume 路径"范围内，
中断与"原生 TUI 附着到该 thread 之后的退出"相关，与 Ctrl-C 按键无关，
也与"任意第二个客户端断开"无关。

**未验证 —— 机制与归因都还没有定位，不要当成已证结论**：

- **`exit-force` 场景没有接中继**（该场景直接 `terminate(force=True)` + `close()` 关掉 PTY）。
  因此**不能**对 force 分支宣称"`turn/interrupt` 计数为 0"，也**不能**宣称
  "TUI 没有机会发包" —— 该场景根本没有观测通道。
  "计数为 0" 这个观测**只对 `--scenario tap` 的中继覆盖窗口有效**。
- **`terminate(force=True)` / `close()` 的具体生效路径未确认**：不知道 TUI 是立即被杀、
  还是在 ConPTY 关闭过程中跑了一部分关闭逻辑；这直接影响归因。
- 因此"是不是 TUI 显式发了中断请求"这一条**尚未排除**；只能说在中继覆盖的那次运行里
  没看到。需要 app-server 侧 debug 日志，或给 force 分支补上中继，才能定位。
- exit 两个场景各自**没有内置同批对照 thread**，且各自只运行一次，未做重复性统计。

**在已测范围内的产品含义**：若用户 attach 原生 TUI 并 `resume` Pan 正在驱动的 thread，
然后交还 Pan，则**在该安装版上**观察到 Pan 那一轮 turn 被判为 `interrupted`。
这与 brief 的"保留运行中任务、无中断切换"存在冲突；是否在所有版本/所有路径上如此，
未经测试（见 §5）。

### 3.6 审批（文档 + 未验证）

官方文档：审批是 **server → client 的 JSON-RPC 请求**
（`item/commandExecution/requestApproval`、`item/fileChange/requestApproval`、
`item/permissions/requestApproval`、`mcpServer/elicitation/request` 等），
带 `threadId` / `turnId`，客户端回 decision。

**未验证**：多客户端同时在线时审批请求发给谁、原生 TUI 能否应答、Pan 与 TUI 是否会
双重应答或互相抢占。原因：要产生真实审批需要模型先请求执行命令，而本轮为完全隔离没有
使用真实凭据；没有找到不依赖模型的审批触发路径
（`thread/shellCommand` 实测文档说明其**在沙箱外直接执行、不继承 thread 沙箱策略**，
因此不产生审批）。
关联风险（**风险假设，本轮未观测到双应答或抢占**）：在被测路径上第二个客户端收不到
turn/item 事件流（§3.4，范围见 §5 未验证项 9），因此审批路由也不应当默认对称；
这是需要后续实验验证的假设，不是已发现的故障。

---

## 4. Pan 当前拓扑与改造点（源码实测）

`packages/core/adapters/codex/app_server_wrapper.py:243-253`：

```python
command = [self.node, self.codex_js, *_server_options(self.extra_options),
           "app-server", "--stdio"]
self.process = subprocess.Popen(command, stdin=PIPE, stdout=PIPE, stderr=PIPE, ...)
```

- `_server_options()`（同文件 `:101-117`）**只保留 `-c key=value`**，其余 flag 丢弃。
- Pan 通过 `_resolve_codex_js()` / `_resolve_codex_node()`
  （`packages/core/adapters/codex/adapter.py:652-692`）把 npm `.CMD` shim 解析成
  `[node, …/bin/codex.js]`，避开 cmd.exe 二次解析的中文乱码。探针**复用同一套解析函数**，
  因此进程形态与生产一致（node → codex.exe 两跳，已实测）。
- 写者模型：wrapper 为每个 thread 维护**单个** `state["turn_id"]`，需要中断时对
  `self.thread_id` + `state["turn_id"]` 发 `turn/interrupt`（同文件 `:860-874`）。
  即 Pan 侧本就假设一个 thread 同时只有一个活跃 turn / 一个写者。

**改造点（推断，未实现；仅适用于"后续想把原生 TUI 接进来"这一目标，不是首版普通
持久 PTY / Web 终端的前置条件）**：把 app-server 从 `--stdio` 换成
`--listen ws://127.0.0.1:<port>`，Pan 作为其中一个 ws 客户端，原生 TUI 作为另一个
（`codex --remote ws://127.0.0.1:<port> resume <id>`）。需要新增：端口分配与回收、
listener 存活探测（`GET /readyz`）、非回环时的鉴权
（`--ws-auth capability-token|signed-bearer-token`）、以及 §3.5 的中断语义处理。

另有**上游既有路径**（本轮只读 help 实测，未运行）：
`app-server proxy --sock <path>` 可把 stdio 字节代理到**运行中的共享 daemon**，
daemon 由 `app-server daemon start` / `remote-control start` 管理，控制套接字为
`unix://`（本机即 §2.2 的 PID 32480）。沿这条路线，Pan 可以继续用 stdio 形态接
共享 daemon，原生 TUI 用 `codex --remote unix://` 接同一个 daemon。
**未验证**：该路线下 Pan 的 per-worker thread 隔离、多 Worker 并发、
以及 §3.5 的中断语义是否改善。

---

## 5. 分层：实测 / 推断 / 未验证

**实测**
- 原生 TUI 可作为第二个客户端连上同一个 ws app-server，并渲染真实 TUI。
- 裸 `--remote` 会自建新 thread；`--remote … resume <id>` 会 attach 到指定 thread。
- attach / detach 期间**后端进程 PID 与身份记录不变**（两次运行各自前后比较）。
- **未被 TUI `resume` 的对照 thread**：其运行中 turn 在 attach 前/中/后保持 `inProgress`
  （观测窗口 65.7s）；Pan 侧连接收到的通知条数继续增长（11→24→26）。
- **被 TUI `resume` 的目标 thread**：attach 期间 `inProgress`，TUI 退出后 `interrupted`
  （Ctrl-C 与 force 两种退出各观测到一次）。
- 第二个客户端走 `thread/resume` 后收不到该 thread 的实时 turn/item 事件
  （安装版 0.159.2 + ws 范围内）。
- 普通 JSON-RPC 客户端 `resume` + 断开**没有**造成该中断。
- 安装版 0.159.2 与本机 0.160.0 上述七条命令路径的 help 文本逐字节一致。

**推断（未直接观测）**
- 目标 thread 的中断与"原生 TUI 附着生命周期"相关，而非 ws 传输或 Ctrl-C 按键
  ——依据是对照 thread 存活、普通客户端断开不中断；但机制未定位。
- Pan 当前 `--stdio` 私有管道在架构上无法被第二个客户端接入（据传输定义）。
- 若将来要做原生 TUI attach，最低形态是 `--listen ws://` + Pan 与 TUI 双客户端。

**未验证（含原因）**
1. **真实模型请求 / 真实任务连续性**：本轮用本地黑洞提供方，模型请求始终挂起，
   因此**没有测到任何模型输出，也没有验证真实任务在 attach/detach 期间的连续性**；
   未使用真实凭据（隔离要求，见 §2.3）。
2. **审批多客户端路由**：需要一个会请求执行命令的模型；无低成本的免模型触发路径。
3. **中断的确切机制与归因**：需要 app-server debug 日志，或给 force 分支补中继。
   `exit-force` 未接中继，该分支无观测通道（§3.5）。
4. **exit 场景的重复性与同批对照**：两个 exit 场景各只运行一次，且各自没有内置对照 thread。
5. **`unix://` 传输、`wss://`/TLS、非回环鉴权**：未测（本机 `unix://` 被既有 daemon 占用，不去碰）。
6. **共享 daemon + `app-server proxy` 路线**：只读了 help，未运行。
7. **0.160.0 运行期行为**：只对比了 help；未在该版本上跑探针。
8. **`resume` 时输入控制权**：本轮没有在 TUI 里真正键入内容提交（只读屏），"谁拿到输入权"未测。
9. **多客户端事件订阅的普适性**：§3.4 的负结果只覆盖"安装版 + ws + `thread/resume`"这一条路径。
   其它传输、其它订阅/接管路径（例如先 `unsubscribe`、或 `experimentalApi` 能力差异）、
   其它 provider 均未测，**因此不能由它推出"所有 provider 多客户端方案都不可能"**。
10. **长时运行**：单次观测窗口约 66s；更长时间下 TUI attach 的稳定性未测。

---

## 6. 接口影响与下一步建议

对 Pan Terminal 公共契约（另由 contract TA 负责）的影响。
下面 2、3 两条是**基于本轮负结果的推荐策略**，不是已被证明的唯一可行做法；
若要改成"依赖 provider 侧广播/多点应答"，先补做 §5 未验证项 2 与 9。

1. **Terminal 与 Agent Session 的所有权应当能表达"同一个 provider 后端、可能有多个客户端"**。
   本轮实测到同一后端确实能接受第二个客户端连接（ws 范围），
   所以"一个 provider 会话只有一个连接"这个前提不成立。
2. **推荐：输出以 Pan 侧为权威扇出点**。在本轮被测的路径上，原生 TUI 拿不到实时
   turn/item 流（§3.4，范围见 §5 未验证项 9），所以让 Pan 从自己那一路事件派生
   "网页显示"与"原生 TUI 显示"是当前更稳妥的做法；作为推荐，不排除将来发现
   provider 广播可用的路径。
3. **推荐：审批尽量由 Pan 单点应答**。本轮**没有**测到多客户端审批路由（§3.6），
   双应答/抢占是**未经证实的风险假设**，不是已观测到的故障。之所以推荐单点，
   是因为 §3.4 已表明本路径下事件分发并非对称，审批同样不应默认对称。
4. **turn 生命周期需要保护**：在 §3.5 的中断语义被解决前，"切到原生 TUI 再切回"
   在已测条件下会牺牲正在跑的那一轮 turn。建议把该场景的验收口径明确为
   "要么不中断，要么显式告警"，而不是默认可用。
5. Windows 下 TUI 需要真实 ConPTY；探针用的是 `pywinpty==3.0.5` 装在**临时 target 目录**，
   未改动全局环境。

**建议的下一步实验（按性价比排序；本轮已决定不做，仅记录）**
1. 给 `exit-force` 分支加 WebSocket 中继 + app-server debug 日志，定位 §3.5 机制。
2. 用受控的、可回答 Responses API 的**本地 stub 模型**（只 stub 上游模型这一层，
   CLI/app-server/TUI 全真）触发一次真实审批，测多客户端路由。需明确标注为 stub。
3. 在**独立临时 home** 中验证 `unix://` 监听与 `--ws-auth capability-token`。
4. 若要做产品原型：先只做"Pan 持 ws 客户端 + 网页自绘视图"，
   把原生 TUI attach 作为第二阶段，避免被 §3.5 阻塞。

---

## 7. 复现方法与清理核对

### 7.1 准备（一次性，独立临时环境，不动全局）

```bash
# 探针依赖 pywinpty；装到临时 target 目录，不污染全局环境
E:/software/miniforge/python.exe -m pip install --target /tmp/codex_probe_env "pywinpty==3.0.5"
```

`websockets`、`psutil` 使用已有的 `E:/software/miniforge/python.exe` 环境。
若路径不同，用 `PAN_PROBE_PYWINPTY` 环境变量指向该 target 目录。

### 7.2 探针与场景

```bash
cd D:/project/pan-worktrees/terminal-codex-explore-20261003

# 版本/能力对照（只跑 --help）
E:/software/miniforge/python.exe audit/terminal/codex/probe_cli_surface_diff.py

# 对照组：TUI 指向无监听端口
E:/software/miniforge/python.exe audit/terminal/codex/probe_codex_remote_tui.py --scenario control

# 订阅语义隔离实验（无 TUI）
E:/software/miniforge/python.exe audit/terminal/codex/probe_codex_remote_tui.py --scenario subscriber

# 退出方式分离实验
E:/software/miniforge/python.exe audit/terminal/codex/probe_codex_remote_tui.py --scenario exit-ctrl-c
E:/software/miniforge/python.exe audit/terminal/codex/probe_codex_remote_tui.py --scenario exit-force

# 主场景（含透明 JSON-RPC 中继，证据最全）
E:/software/miniforge/python.exe audit/terminal/codex/probe_codex_remote_tui.py --scenario tap
```

退出码 0 表示该场景全部断言通过；证据写到
`audit/terminal/codex/evidence/<scenario>-<时间戳>.json`（含后端 PID/创建时间、
turn 状态、TUI 屏文本、中继帧、teardown 各步耗时与结果、清理断言）。
探针带 `faulthandler` 看门狗（默认 300s），卡住会自行 dump 全部线程栈后退出。

### 7.3 已知的探针失败项说明

`tap` / `main` 场景退出码非 0 是**设计如此**：它们把 §3.4（第二个客户端收不到事件）
和 §3.5（TUI 退出后 turn 被中断）当作断言，这两条当前稳定失败。
这是本轮的结论，不是探针缺陷。

### 7.4 清理核对（实测）

| 项目 | 结果 |
|---|---|
| 探针自建的 app-server / TUI / 黑洞端口 | 每次 `teardown_all_steps_ok` 全通过；`listener_released` 通过 |
| 临时 `CODEX_HOME` | 每次 `temp_home_removed` 通过；收尾核对剩余 `codex_tui_probe_*` 数量 = 0 |
| 临时 pywinpty target 目录 | 收尾已删除 |
| 被停止的进程 | 仅本探针自己创建的：smoke server PID 43532；一次卡死运行的 PID 36932(node 48260 → codex.exe 25544) |
| **从未被发信号的进程** | 共享 daemon PID 32480、VS Code 扩展 codex、Pan Worker codex 进程 |
| 共享 daemon 收尾核对 | PID 32480 仍存在，其 `CreationDate` 仍为 2026-10-02 20:47:42 **未变** |
| 真实 `~/.codex` | 全程未读写（探针只用临时 home） |

**进程身份断言的口径（避免误读）**：探针记录的是 **PID + 该进程自己的 `create_time`**
（psutil 把 Windows FILETIME 换算成 Unix 秒），断言方式是
**"同一个 PID 在 attach 前 / attach 中 / detach 后的记录值互相比对是否未变"**。

- 这验证的是**同一个进程没有换人**（PID 复用/重启会被时间戳变化暴露），
  不是"node 与它的 `codex.exe` 子进程创建时间相等"——它们是**两个不同进程，
  各自记录、各自比较**，两者时间戳数值本来就不要求相等（实测中常只相差不到 1 秒，
  但这是巧合而非断言内容）。
- 表中所写"后端身份记录未变"一律指上述**各自前后比对**的含义。

备注：会话期间观察到探索开始时的两个 Pan Worker codex 进程（00:12 / 00:17 创建）在结束时
已不存在。**本探针从未向任何非自建进程发送过信号**；它们的退出与本轮探针没有已知因果，
如需确认请查 Pan 侧 Worker 生命周期记录。

### 7.5 本轮一次探针卡死的记录

`tap` 场景首次运行时在 teardown 阶段卡死（主线程停在 asyncio 事件循环，子进程仍在）。
用 `py-spy dump` 定位到是收尾步骤未收敛。已修正并加固：
`WsTap` 改为轮询收包 + `_closing` 标志，收尾各步改为 `asyncio.wait_for` 有界等待，
并加入 `faulthandler` 看门狗。修正后 4 次 tap/main 运行均在 60–90s 内正常结束。

---

## 8. 官方参考

来源限定在 OpenAI 官方文档域（`learn.chatgpt.com`，即 `developers.openai.com` 的重定向目标）：

- Codex App Server（连接 CLI 终端 UI、传输、thread/turn 原语、订阅、审批）：
  <https://learn.chatgpt.com/docs/app-server>（原始 markdown：
  <https://learn.chatgpt.com/docs/app-server.md>）
- 配置参考（`check_for_update_on_startup`）：
  <https://learn.chatgpt.com/docs/config-file/config-reference.md>
- 文档索引（`llms.txt`）：<https://learn.chatgpt.com/llms.txt>

本报告引用到的关键官方表述：
`--remote` 接受 `ws://` / `wss://` / `unix://` / `unix://PATH`；
`--listen` 支持 `stdio://`（默认）/ `unix://` / `unix://PATH` / `ws://IP:PORT` / `off`；
ws 监听同时提供 `GET /readyz` 与 `GET /healthz`；
`thread/unsubscribe` 解除**当前连接**对该 thread 的订阅，最后一个订阅者离开后
线程保留到"无订阅且无活动"满 30 分钟才卸载；
`turn/steer` 向进行中的 turn 追加上下文；`turn/interrupt` 使其以 `interrupted` 结束。
（均为官方页面原文，本轮未逐条实测。）

---

## 9. 交付物清单

| 文件 | 说明 |
|---|---|
| `docs/design/PAN_TERMINAL_CODEX_FEASIBILITY_20261003.md` | 本报告 |
| `audit/terminal/codex/probe_codex_remote_tui.py` | 主探针（control / subscriber / exit-ctrl-c / exit-force / main / tap 六个场景） |
| `audit/terminal/codex/probe_cli_surface_diff.py` | 安装版 vs 本机上游构建的只读能力对照 |
| `audit/terminal/codex/evidence/*.json` | 全部运行证据（含 PID/创建时间、turn 状态、中继帧、屏文本、清理断言） |

本轮交付**可运行探索**与可复现证据，**不包含** Pan 侧终端功能实现。
