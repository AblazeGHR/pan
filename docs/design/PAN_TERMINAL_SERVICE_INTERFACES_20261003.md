# Pan TerminalService 接口与预算纪律（P2 第一批，2026-10-03）

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
- `resize` **分列**返回：`pty.accepted`（runner 接受）与
  `engine.confirmed`（仿真器确认），并显式给出 `three_way_agreement = None`
  ——**不宣称三方一致**。

## 8. close / shutdown：三项证明才写终态

`close(terminal_id, reason=...)`：

1. **先记 CLOSING**（`registry.update` → `EXITING`）；
2. 停心跳；向 runner 发 **一次** stop（阻塞部分被 `_BoundedCall` 追踪：重试**复用**
   同一在途/已完成调用，**不叠加**、不发第二个 stop，迟到成功被缓存消费）；
3. 每轮**重新评估**外部证据（早期实现把整个判定缓存进 worker，导致后补齐的证明
   永远读不到 —— 已修为"worker 只覆盖阻塞 stop，证据每轮重评"）。

**三项证明齐备**才记 `exited` 并删秘密：

| 证据 | 来源 |
| --- | --- |
| ① runner 收尾确认 | stop 响应 `status == "exited"` |
| ② launcher 引擎收尾 | `launcher-status/<tid>.json` 的 `engine.cleanup.converged is True` **且** `launcher_identity` 与 hello 自证身份精确一致 |
| ③ 经身份核验的进程退出 | **我们派生**的 `Popen` 句柄 `poll()` 返回非 `None`（该句柄即 `CreateProcess` 返回值，绑定证据、无 PID 复用窗口） |

任一缺失 → 记录停在 `EXITING` / `CLEANUP_FAILED` + **静态 reason**，**保留**
owner/record/secret 可重试；**不标 exited/lost**；**绝不使用 `caller_responsible`
绕过**（删除秘密只走 `verified_exit=True`，且只在已证明终止之后）。

`shutdown(budget=...)`：按**总预算**（含锁等待）尽力收敛 `service` 所有终端；
`detached` 的按 detach 语义**不停止**（`kept_detached`，保 PTY/PID/秘密/记录）；
预算耗尽 → `budget_exhausted=True` + `unconfirmed` 列表 + `secrets_retained=True`，
未收敛部分**保留**可重试。本实例无句柄的跨进程遗留记录 → `shutdown-unattached`
（**不冒充**已收敛）。

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

- **本实例仍持有派生句柄**：以该句柄（绑定证据）判我们派生的进程是否退出；退出且
  已证明 → `dead-confirmed`（补记 `exited` + 删秘密）；仍存活 → 按记录状态分列
  （`CLEANUP_FAILED`/`EXITING` → `cleanup-unconfirmed`；`detached` → 核对后重连）。
- **只有持久记录（跨进程/重启）**：先查秘密是否存在（缺失 → `unattributable`），
  再按 pid + raw FILETIME **精确匹配**的三态探针判定：
  - `alive`（身份精确匹配且存活）；
  - `dead-confirmed`（身份精确匹配且同句柄已退出）→ 补记 `exited` + 删秘密；
  - `unattributable`（探针缺失 / `UNKNOWN` / FILETIME 不符）→ **零终止**、保诊断、
    **保留记录与秘密**。
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
