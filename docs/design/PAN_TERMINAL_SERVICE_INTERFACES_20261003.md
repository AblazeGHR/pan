# Pan TerminalService 接口与预算纪律（P2 第一批，2026-10-03；r2 返工 / r3 窄修后修订）

> **r2 修订（独立审查 `d7905408` 判返工后）**：F1–F10 已闭环。本节标 **r2** 的条目
> 覆盖首版语义；未标者沿用首版。首版口径**不再适用于**被 r2 修订的条目。
>
> - 变更记录见 §13；逐项映射与实测见
>   `audit/terminal/implementation/service/r2/README.md`。
> - r2 只改 `packages/core/terminal/service.py` 与本文；launcher 与所有共享模块
>   **只读**，旧证据与原测试**未改**。

- 任务：T-TERMINAL-PTY-20261003 的 **P2 第一批：服务控制层**（`TerminalService` +
  registry 生命周期闭环）。**不含** HTTP/WS/MCP 入口、前端与 lifespan 接线。
- 工作树：`D:/project/pan-worktrees/terminal-service-implement-20261003`
  （branch `implement/terminal-service-20261003`，起点 `6b6118ef`，生产代码基线
  `475395c3`）。
- 性质：**接口成稿 + 纪律声明**。下游（REST/WS/MCP/lifespan）按本文与
  `packages/core/terminal/service.py` 接线。
- 依据（只读）：`PAN_TERMINAL_IMPLEMENTATION_PLAN_20261003.md`（§5.5、§13）、
  `PAN_TERMINAL_COMPOSITION_BOUNDARIES_20261003.md`（I1–I4）、
  `PAN_TERMINAL_RUNNER_INTERFACES_20261003.md`、`PAN_TERMINAL_IPC_INTERFACES_20261003.md`、
  `PAN_TERMINAL_LAUNCHER_INTERFACES_20261003.md`、`PAN_TERMINAL_CORE_INTERFACES_20261003.md`
  §4.5/§6/§7。

## 1. 写范围与实际改动

| 文件 | 性质 |
| --- | --- |
| `packages/core/terminal/service.py` | **新增**：服务控制层 |
| `tests/test_terminal_service.py` | **新增**：确定性 + 真实 Windows 隔离测试 |
| 本文件 | **新增**：接口与预算 |
| `audit/terminal/implementation/service/**` | **新增**：证据（新目录，旧证据不动） |
| `packages/core/terminal/launcher.py` | **最小接线**：新增可选 `cwd` / `shell_argv` 并透传给已有 `TerminalRunner(cwd, shell_argv)`；**默认值不变**（未传时 runner 仍用 `cmd.exe /q /d`、不设 cwd） |

未改：`contracts.py` / `registry.py` / `attachments.py` / `runtime.py` / `ipc.py` /
`win_pipe.py` / `secret_store.py` / `backend.py` / `runner.py` / `runner_client.py` /
`emulator.py` / `__init__.py` / server / MCP / frontend / 依赖清单与锁 /
`background_jobs` / `.workflow`。

### 1.1 launcher 的最小接线（为何必要且安全）

`TerminalLauncher` 原签名无法表达"真实 cwd / 替代 shell"，而 P2 必须能把终端落在
**指定临时目录**里做真机验证。因此只做两件事：

1. `TerminalLauncher(..., cwd=None, shell_argv=None, ...)`：默认 `None` 时
   `_build_runner` 传给 `TerminalRunner` 的仍是 `cwd=None` / `shell_argv=None`
   ——与接线前**逐字等价**（runner 侧 `DEFAULT_SHELL_ARGV = ("cmd.exe","/q","/d")`）。
2. CLI 新增 `--cwd` 与 `--shell-argv`（JSON 数组，缺省/空串 = 不改变默认）。
   非法 JSON → 退出码 2（用法错误），不落盘、不回显 argv 原文。

清理机制、退出码合并、引擎收尾、状态文件一律未改；`runner_factory` /
`emulator_factory` 注入语义不变。

## 2. 公开面（同步）

```python
TerminalService(
    root=None, *, registry=None, secret_store=None,
    spawn_launcher=None, client_factory=None, identity_probe=None,
    capacity=8, heartbeat_interval=1.0, lease_grace=2.0,
    stop_confirm=5.0, shutdown_budget=20.0, startup_timeout=30.0,
    shell_argv=None, python_argv=None, repo_root=None, log_stderr=True,
) -> None

.create(*, rows=24, cols=80, cwd=None, workspace_id=None, session_id=None,
        context=None, terminal_id=None, shell_argv=None, timeout=None) -> dict
.list() -> list[dict]
.get(terminal_id) -> dict
.describe() -> dict
.read(terminal_id, cursor=0, *, max_bytes=None) -> dict
.snapshot(terminal_id, *, timeout_ms=5000) -> dict
.input(terminal_id, token, data, *, seq=None) -> dict
.resize(terminal_id, token, rows, cols) -> dict
.attach(terminal_id, client_id, *, role="observer", rows=0, cols=0) -> LeaseToken
.release_attachment(token) -> None
.control_holder(terminal_id) -> dict | None
.close(terminal_id, *, reason="explicit-close") -> dict
.detach(terminal_id) -> dict
.reconcile() -> dict
.shutdown(*, budget=None, reason="service-shutdown") -> dict
```

- **只公开同步面**（本批的既定选择）。阻塞原语（管道/DPAPI/进程等待）**不得**在
  事件循环线程直接调用：下游适配器必须 `await asyncio.to_thread(...)` 或投工作线程。
- 本层自己只用两类线程：每终端一个**心跳线程**、每终端最多一个**有界 close worker**。
- 无 async 变体；`shutdown()` 是本层的"批次收尾"原语（lifespan 接线在下一批）。

### 2.1 预算（调用方侧有界等待，**非** OS 硬 SLA）

| 常量 | 默认 | 含义 |
| --- | --- | --- |
| `DEFAULT_CAPACITY` | 8 | 并发容量准入（同时未终结的终端数） |
| `DEFAULT_HEARTBEAT_INTERVAL_SECONDS` | 1.0 | 所有者心跳间隔（组合边界 I4） |
| `DEFAULT_LEASE_GRACE_SECONDS` | 2.0 | lease 死期（与核心契约同值） |
| `DEFAULT_STOP_CONFIRM_SECONDS` | 5.0 | 单终端 close 等真实收尾确认 |
| `DEFAULT_SHUTDOWN_BUDGET_SECONDS` | 20.0 | shutdown **总**预算（**含锁等待**） |
| `DEFAULT_STARTUP_TIMEOUT_SECONDS` | 30.0 | 启动等 hello 自证 + 首绑 |

这些预算是**本层的调用方侧有界等待**：若被注入的原语违反自身 timeout 契约而阻塞，
本层不提供独立兜底上界（与 launcher F1 同一诚实口径）。

## 3. 生命周期与状态映射

- 本层**不新增枚举**。计划里的 CLOSING / stopping 映射到既有合法枚举
  `RuntimeState.EXITING`；未获证明的终态用 `CLEANUP_FAILED`。
- `create` 记录以 `STARTING` **先落盘**，四门确认后才改 `RUNNING`。
- 停止原因只用组合边界 I1 的冻结词表：`explicit-close` / `service-shutdown` /
  `close`（回调）；`lease-expired` 由 runner 自行使用，本层不新造词。

## 4. create：顺序与"不认 Popen.pid"

1. **容量准入 + 记录持久化在同一临界区**（`_admission_lock`）：计数 → 分配 id →
   `registry.create`。因此并发 create **不会**超限；超限时**零记录、零进程**。
   （实测缺陷：先计数后释放锁会让 6 个并发 create 全部落盘 —— 已修。）
2. 派生**生产 launcher**（`python -m packages.core.terminal.launcher`，DETACHED），
   argv 只有 `--terminal-id` / `--secret-file` / `--rows` / `--cols` 与可选
   `--cwd` / `--shell-argv`；**无 token**（env 也过滤 `PAN_TERMINAL_*`）。
3. **hello 自证 + 同句柄 pid/raw FILETIME/Wait 内核核验**
   （`wait_for_bootstrap_identity`，默认 `verify=True`）→ 才写 DPAPI 秘密
   （`complete_bootstrap`）。
4. `RunnerClient.attach()`：端点身份核验 + HMAC 双向认证 → 才允许业务请求。
5. `describe().runner_state == "running"`（四门确认）→ 才发布 `RUNNING`。
6. 启动**独立心跳**（自己的连接 + 稳定 `client_id = pan-owner-<terminal_id>`）。

**身份权威**：记录里的 `pid` / `process_created_at_filetime` 是 hello 自证且经内核
核验的 **runner 身份**，**不是** `Popen.pid`。`Popen.pid` 只记在内存（`spawn_pid`）
用于交叉核验。uv 的 python 启动器可能是 shim（`Popen.pid` ≠ 真解释器 pid），因此两者
**分别记录、分别判定、分别清理**；**绝不**按命令行/进程名广杀。

## 5. 心跳（与浏览器 attachment 分层）

- 心跳走**自己的** `RunnerClient` 连接 → 慢 `snapshot`/`input`/`close` 不拖住心跳。
- `client_id` 按终端确定（`pan-owner-<terminal_id>`）→ 跨重连稳定，同 id 续约不增
  generation（组合边界 I4）。
- 单线程串行、每轮 `heartbeat(timeout_ms=2000)` 有界；连续失败超过死期即
  `describe().heartbeats[tid].lost = True`（**如实上报**，不掩盖、不重试风暴）。
- attachment（浏览器）lease 是**另一层**：`AttachmentRegistry` 的 `client_id` 与
  心跳的 `client_id` 互不相干；`input`/`resize` 只认 attachment lease。
- 断开 attachment（`release_attachment`）**只撤销 lease**，永不触碰 runtime 寿命。

## 6. 读与快照：不补零、不跳 cursor、不升级

`read()` 返回 `data`（从 `seq` 起的事实）、`next_cursor`、`gap`、
`first_retained_seq`、`fresh_view_required` 与 `zero_fill=False`：

- **gap 不补零、不跳 cursor**：游标只取协议给出的 `next_cursor`，服务不自行推进；
- 缺口时 `fresh_view_required=True`，由显示层决定走快照或明确降级 fresh-view；
- **不自动 `reset_baseline`、不杀 PTY**（`auto_reset_applied=False`）。

`snapshot()`（协议 A）透出 `serialized_screen` + **applied cursor** +
`fidelity` / `recovery` / `feed_lag` / `note` / `engine` / `diagnostics`，并：

- `cursors_valid` / `reset_unconfirmed` 经 `_bool_or_none` → **只接受真 bool**，
  缺失或字符串（如 `"true"`）一律 `None`（unknown 不造值）；
- **`diagnostics.reasons == []` 不升级为 full**：`fidelity` 原样保留；
- `applied_evicted`：applied cursor < 保留窗口起点时为 `True`（用一次轻量
  `describe` 取 `first_retained_seq`；取不到 → `None`，不猜）；
- `continuation_hint ∈ {continue-stream, partial-view, fresh-view-required,
  retry-after-start}` —— **只降级、不升级**。

## 7. input / resize：控制权与分列确认

- `input` / `resize` **必须**带本服务签发的 `LeaseToken`；经
  `AttachmentRegistry.send/resize`，**校验与调用在同一（每终端）临界区** → 撤销后
  **零新写**（实测断言：`client.calls["input"]` 不再增长）。
- observer token → 稳定 `NotControlLeaseError`（不因世代/撤销而误伤）。
- **r2 / F8：关闭准入门早于 RPC**。lease 目标解析（`_lease_lookup`）读**磁盘
  registry**（权威）而非内存副本；close / 未证明路径在写盘后**同步**内存态
  （`_sync_state_record`）。首版内存态仍 `running` 时会让 `input` 在 close 之后仍
  触达 client（纵深防御缺口：runner 侧 `_closing` 会拒，但服务层门失效）。
- `resize` **分列**返回：`pty.accepted`（runner 接受）与
  `engine.confirmed`（仿真器确认），并显式给出 `three_way_agreement = None`
  ——**不宣称三方一致**。

## 8. close / shutdown：三项证明才写终态

`close(terminal_id, reason=...)`：

1. **先记 CLOSING**（`registry.update` → `EXITING`）并**同步内存态**（r2：见 F8）；
2. 停心跳；向 runner 发 stop —— **r2（F2）**：在途**单飞复用**；已完成但**未确认**
   （`closing` / 报错）且证据未齐备时，下一轮**有界幂等重发**（`MAX_STOP_RESENDS = 2`，
   仅在 worker **确已结束**时才重发，绝不与在途调用叠加）；**已确认成功不机械重复**。
   runner 侧对重复 stop幂等（已 `exited` 返回 `exited`），故重发安全；
3. 每轮**重新评估**外部证据。

**三项证明齐备**才记 `exited` 并删秘密：

| 证据 | 来源 |
| --- | --- |
| ① runner 收尾确认 | stop 响应 `status == "exited"` |
| ② launcher 引擎收尾 | `launcher-status/<tid>.json` 的 `engine.cleanup.converged is True` **且** `launcher_identity` 与 hello 自证身份精确一致。**launcher 以 `exit 6`（`LAUNCHER_EXIT_CLEANUP_UNPROVEN`）退出时 `converged` 为 false → 不算成功**（r2 / F1） |
| ③ 经身份核验的进程退出 | `Popen` 只有在其 PID 精确等于 bootstrap runner PID 时提供该 runner 的绑定退出证据。Python shim PID 不同时，生产启动期间核验并保留真实 runner 句柄（PID+raw FILETIME+ALIVE），关闭时须同句柄 signaled；包装进程退出不算 runner 退出。无自持句柄的跨进程遗留记录仍须身份三态 `dead-confirmed`，UNKNOWN 不造证明。新增句柄 probe/close 非阻塞串行，CloseHandle 失败保 owner、秘密和可重试状态。 |

任一缺失 → 记录停在 `EXITING` / `CLEANUP_FAILED` + **静态分类** reason，**保留**
owner/record/secret 可重试；**不标 exited/lost**；**绝不使用 `caller_responsible`
绕过**（删除秘密只走 `verified_exit=True`，且只在已证明终止之后）。

**r2：close 的 reason 为内部静态分类**（F10），**不再回显调用方传入的 reason**：

| reason | 含义 |
| --- | --- |
| `runner-stop-unconfirmed` | 缺证据①：stop 未确认（`closing` / 报错 / 重试用尽） |
| `engine-cleanup-unproven` | 缺证据②：引擎收尾未确证（`exit 6` / status 缺失或损坏） |
| `process-still-running` | 缺证据③：进程仍在（无句柄且身份未判死） |
| `cleanup-unconfirmed` | 兜底（组合缺失） |
| `close-in-flight` | stop 仍在途，预算耗尽 |
| `close-lock-wait-timeout` | 取 `state` 锁超出预算（r2 / F6） |

`shutdown(budget=...)`（r2 语义）：

- **先关 create 准入**（F9）：在 `_admission_lock` 内置 `_closing_down`，与并发
  `create` **线性化** —— 要么 create 先落盘并被本次 shutdown 收敛，要么被拒
  （`ServiceClosingDown`），**不产生无人收敛的新终端**。重试既有 cleanup 仍允许；
- 按**总预算**收敛 `service` 所有终端；`detached` 按 detach 语义**不停止**
  （`kept_detached`）；
- **F5**：**只**释放**已收敛 / kept-detached / 已终态**的连接；未收敛项**保留**
  `client` 与 `close_call` 引用（`retained_for_retry`），使其后续仍可重试收敛；
- **F6**：总预算**含**取锁等待、worker 等待、停心跳与释放等待；`budget=0`
  **不被抬高**（按 ~0 处理），报告新增 `elapsed_seconds`，`elapsed_within_budget`
  **如实**反映超时；
- **F7**：预算耗尽后**未处理**的记录**全部**列入 `unconfirmed`（另单列 `skipped`），
  `secrets_retained = bool(unconfirmed ∪ skipped)`，**不得假报 False**。


## 9. detach：真实环境显式拒绝，零状态变化

`detach(terminal_id)` 透传 runner 的 `stop(reason="detach")`：

- 真实宿主（ambient Job）**不支持** durable detach → runner 显式拒绝时返回
  `{"detached": False, "status": "detach-refused", "state_changed": False,
  "durability": {...}}`，**零状态变化**：不改记录、不停心跳、不删秘密、
  **不做 breakaway / 环境逃脱实验**；
- 成功时记录 `owner="detached"` / `detached=True` / `detached_at`，停心跳、
  **保留秘密**（跨 Pan 重启重连闭环靠它），返回 `mechanism="runner-reported"`。
  注入替身下的"已 detached"只验证逻辑，**报告明确机制是注入**，不称真实 durable 验收。

## 10. reconcile：分列，不冒充恢复

`reconcile()` 返回五个互斥桶：`dead-confirmed` / `unattributable` /
`cleanup-unconfirmed` / `alive` / `already-terminal`，并显式声明
`fresh_pid_absence_is_dead_evidence: False`。

**r2 / F1（核心纪律）**：**我们派生的进程句柄退出 ≠ 整树终止证明**。launcher 可能
以 `exit 6`（引擎收尾未确证）退出；shim 场景下句柄 pid 还可能**不是** runner pid。
因此写 `exited` / 删秘密**必须**以 **runner 身份三态 = `dead-confirmed`** 为门：

- 身份 `dead-confirmed`（**pid 与 raw FILETIME 都精确匹配**且同 handle `Wait` 已
  退出）→ 才补记 `exited` 并删秘密；
- 身份 `alive` → `cleanup-unconfirmed`，**保秘密 + 保记录**（launcher 走了但
  runner 仍活着；删秘密不可逆）；
- 身份 `unattributable`（`UNKNOWN` / **PID 不符** / FILETIME 不符）→ **零终止**、
  保秘密 + 保记录。

**r2：身份比对必须同时核对 PID 与 FILETIME**（首版只比 FILETIME）。"错 PID +
同 FILETIME" 在 PID 复用下**不**是同一进程 → `pid-mismatch` → 不可归因。

**r2 / F3：跨进程遗留 managed / cleanup-failed 记录的重发 stop 入口**（首版完全没有
该入口，导致重启后永远无法收敛）：

- 前置门：秘密在位 **且** 身份三态为 `alive`（pid+FILETIME 精确匹配）；
- 死期等待**有界**（`lease_grace + 0.2`，单次调用内完成）；
- 经**端点核验**（秘密 + HMAC + `describe`）重连后发 stop；**不建心跳** ——
  **不**通过续约复活旧 managed；
- 成功仍需三项真实证明；**不拥有** self-spawn 句柄时不造证明（见 §8 证据③）；
- 失败保 owner/record/secret 可重试；同一记录本实例**只尝试一次**
  （`_persisted_stop_attempts`），避免反复打扰 runner；
- 端点不可核验（attach 失败 / 状态不符）→ `unattributable`、零终止。

**r2 / F4：重连必须先回收旧资源**——已有可用连接则**复用**，否则**先停旧心跳、
再释放旧连接**然后才建新连接。否则重复 `reconcile` 会让心跳线程与连接**无界增长**
（首版实测 1→2→3→4 个同名心跳线程、release 恒为 0）。

- **PID 查不到不是 retained DEAD 证据**（`UNKNOWN` ≠ 已死）：不据此改终态、不删凭据。
- **不创建同 id 替代 runner**、**不复活旧 lease**（旧 token 仍 `StaleLeaseError`）、
  **不因客户端断连删秘密**。
- 身份不可核验与清理未确认**分列**：前者是"无法归因"，后者是"未收敛"，都不是恢复。


## 11. 公共输出面与错误纪律

- `create/list/get/close/detach/read/snapshot/input/resize/reconcile/shutdown` 的
  返回值**不含 token / pipe / 秘密内容**（`pipe` 字段不出现在任何公共视图；实测断言
  `"pan-terminal-" not in blob`）。
- `process_created_at_filetime` 以**十进制字符串**对外（raw64 ≈ 1.3e17 超 JS 安全
  整数，与 `identity.filetime_json` 同口径）。
- 错误类型：`CapacityExceeded` / `StartupFailed` / `CleanupUnconfirmed` /
  `DetachRefused` / `TerminalNotAttached` / `ShutdownBudgetExhausted`，均只带
  **静态 reason**（可枚举）与 `error_type`（**异常类型名**）；无自由文本、无路径、无秘密。
  记录不存在时 `UnknownTerminalError` 原样冒泡（调用方需区分"不存在"与"未连接"）。
- 诊断有界：`describe().events` 上限 64 条，每条 event ≤ 48 字符 / detail ≤ 64 字符。
- `scope`（workspace/session）**仅元数据，不是权限**；`created_by` 来自调用上下文
  （`ServiceContext`），**不从目标 `session_id` 推断**。
- `ServiceContext.trusted_local=True` 只表示"同用户本地受信控制器"这一接线前提：
  本层**不**实施 Origin / CSRF / MCP caller gate，也**不**据此声称存在 Web 鉴权
  （真实鉴权是下一批的接线责任）。

## 12. 明确未承诺 / 未验收

- **本批不接** HTTP/WS/MCP/前端/lifespan；无 `async` 面。
- **Ctrl-C**：未验收（不承诺 OS 语义）。
- **真实 durable detach**：ambient Job 下**维持拒绝**；注入的 detached 生命周期只验证
  逻辑，**不称真实 durable 验收**。
- **浏览器渲染/fit、Origin/CSRF/MCP caller gate、provider/账号/网络服务、跨用户/跨主机、
  POSIX、长稳/慢客户端背压、跨 sidecar 重启恢复**：均未验收。
- 预算是**调用方侧有界等待，非 OS 硬 SLA**；真实会话 `partial` 是常态
  （未验证 VT 序列不升级）；F5 确认字段是**保守近似、无历史世代原子绑定**。
- Job 内核退出兜底只按**已测布局**陈述（launcher 硬死 → 整树消亡），不泛化为任意部署
  的通用保证。
- 容量准入是**单服务实例内**的硬约束（`_admission_lock`）；跨进程并发创建同一数据根
  不在本批保证范围（`registry` 无跨进程容量原语，未擅自扩展共享协议）。

## 13. 变更记录

- `2026-10-03` **首版**：`service.py` 交付，41 项测试。
- `2026-10-03` **r2（独立审查 `d7905408` 判返工后）**：**仅改 `service.py` 与本文**；
  launcher 与所有共享模块只读，旧证据与原 `tests/test_terminal_service.py` 未改。

  | # | 修订 |
  | --- | --- |
  | F1 | `reconcile` 活状态路径**以 runner 身份三态为门**（句柄退出 ≠ 终止证明）；ALIVE/UNKNOWN/身份不符 → 保秘密+保记录；`exit 6` 引擎未确证不算成功；身份比对**同时核对 PID 与 FILETIME**（新增 `pid-mismatch`） |
  | F2 | stop **有界幂等重发**（`MAX_STOP_RESENDS=2`）：在途单飞复用；已完成未确认才重发；已确认不重复；`_BoundedCall.reset()` 仅在 worker 结束后可用 |
  | F3 | 跨进程遗留 managed/cleanup-failed 的**重发 stop 入口**：秘密+身份核验 → 有界死期等待 → 端点重连（不建心跳、不复活）→ 三项证明；无 self-spawn 句柄时用身份三态且不造证明；每记录只试一次 |
  | F4 | `_reconnect` 先**复用或真实回收**旧连接/心跳，消除线程与连接无界增长 |
  | F5 | `shutdown` **只**释放已收敛/kept-detached/已终态的连接；未收敛项保留 `client`/`close_call` 供重试 |
  | F6 | 总预算**含**取锁（`lock.acquire(timeout=…)`）、worker、停心跳、释放等待；`budget=0` 不抬高；新增 `elapsed_seconds`，`elapsed_within_budget` 如实 |
  | F7 | 预算耗尽后 skipped 记录**全部**列入 `unconfirmed`（另单列 `skipped`），`secrets_retained` 不假报 |
  | F8 | 关闭准入门**早于** RPC：`_lease_lookup` 读磁盘 registry；close/未证明后**同步**内存态 |
  | F9 | `shutdown` **先关 create 准入**（`_closing_down`，与并发 create 线性化）；新增 `ServiceClosingDown` |
  | F10 | close 的 reason 改为**内部静态分类**（`runner-stop-unconfirmed` / `engine-cleanup-unproven` / `process-still-running` / `close-in-flight` / `close-lock-wait-timeout` / `cleanup-unconfirmed`），不再回显调用方 reason；无自由文本 |
  | F12 | 测试侧：心跳断言改**有界等待**（产品实现无需改动） |

  **锁序（F6 统筹，刻意避免死锁）**：`_admission_lock` → `_global_lock` →
  `state.lock`；`state.lock` **只**在 `_close_state` 内以**有界** `acquire(timeout)` 取得，
  其余路径（`_mark_unproven` / `_stop_heartbeat` / `_require_client` /
  `_release_client`）**不取**该锁——属性赋值在CPython 下是原子的，避免"等锁超时后
  再取同一把锁"造成二次挂死。

- `2026-10-03` **r3 窄修（MA 已决修法的执行；只改 `service.py` + 本文 + 回归 +
  本目录证据）**：

  | # | 修订 |
  | --- | --- |
  | 1 | `_identity_evidence`：**缺 `observed_pid` → `unattributable`（`pid-missing`）**——FILETIME 单独相同不足以认定同一进程；PID/FILETIME **非整数或转换失败**归入静态分类（`pid-invalid` / `filetime-missing` / `filetime-invalid` / `identity-missing`），**不抛异常、不伪造匹配**。正控（真 PID+FT）/ 错 PID / 缺 PID 三类均有门控 |
  | 2 | `_close_state`：`call.reset()` 返回 `False` 时**保留/重读原调用**结果（此前 `finished2` 未初始化 → `UnboundLocalError`），不读未定义变量、不假收敛；同终端"判定+重发"由**独立 `close_op_lock`** 串行（`threading.Lock`），锁等待计入本次 deadline，且**不包裹**阻塞 stop 调用（避免让其它预算路径无界） |
  | 3 | `shutdown`：**入口即起 deadline**（取锁等待不再发生在 `started` 之前）；准入关门请求**不因取锁超时丢失**——先置无锁 `Event`，`_admit` 在准入临界区复查 `标志 or Event`；准入锁**有界**取锁，超时如实继续收敛；`elapsed_within_budget` 仅容忍 1ms 浮点噪声（**不得**再把超时报成 within）。**不**把普通 registry I/O 声称为 OS 硬 SLA |
  | 4 | 跨重启端点**可重复**恢复：`_retry_persisted_stop` 不再是"一次 attempt 永久闩锁"；身份/秘密**每轮重核**，端点连接抽为 `_attach_persisted_stop_client`（失败**局部真实 `release_connection`**、保留**可重试 state**）；`_reconcile_live` 对"无句柄且无 client"的 cleanup-failed 记录**重新路由**回该路径（此前直接返回、永不再 attach）。**不**造 process 句柄、**不**启动 managed 心跳；成功仍只凭原三项证据 |

  r3 未改共享协议/未扩架构；`_FakeProbe`（原测试替身）新增**显式 `pid` 参数**，需要
  "匹配"语义的调用点传记录真实 PID——**不放宽生产门、不删断言、不触碰心跳卫生区**。

