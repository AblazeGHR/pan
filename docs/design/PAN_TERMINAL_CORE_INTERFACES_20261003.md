# Pan Terminal 公共核心接口冻结（P0，2026-10-03）

- 任务：T-TERMINAL-PTY-20261003 的 P0「契约落地」交付。
- 工作树：`D:/project/pan-worktrees/terminal-core-implement-20261003`
  （branch `implement/terminal-core-20261003`，起点 `9e7e0c2f`）。
- 性质：**接口冻结文档**。后续 TA（Windows backend / runner / service / 前端）
  按本文与 `packages/core/terminal/contracts.py` 的签名实现，不改核心签名；
  变更走记录。
- 输入（只读）：实施计划 §3.1/§8.5、契约报告与契约原型
  （`audit/terminal/contract/pty_contract.py`，参考不直接生产化）。
- 重要边界（防误用）：
  - 本核心**不含**任何 Windows/ctypes/pyte 导入副作用；所有平台依赖延迟导入；
  - 本核心**不含**仿真器实现：`AuthoritativeEmulator` 只在内声明协议，
    `AppliedSnapshot.cursor` 语义为**已解析位置**（见 §4.5）；
  - 启动门禁的 bool 证据**不是** OS 级保障（见 §5）；
  - `PyteScreenObserver` / `PyteEmulator` 类路径一律 `fidelity=partial`，不承担
    网页 TUI 状态恢复；浏览器不是权威。

---

## 0. 交付与写范围

| 文件 | 内容 |
| --- | --- |
| `packages/core/terminal/contracts.py` | 平台无关数据、枚举、异常与全部 Protocol（冻结） |
| `packages/core/terminal/output.py` | `OutputLog`（有界、绝对字节偏移、gap） |
| `packages/core/terminal/runtime.py` | `PtyRuntime`（drain、状态机、清理、detach） |
| `packages/core/terminal/ownership.py` | `UnverifiedOwnershipGate`、`NullTreeGuard`、`build_runtime` |
| `packages/core/terminal/registry.py` | `TerminalRegistry`（原子写 + 跨进程锁） |
| `packages/core/terminal/attachments.py` | `AttachmentRegistry`（control lease） |
| `packages/core/terminal/observer.py` | `PyteScreenObserver`（延迟导入、partial） |
| `packages/core/terminal/driver.py` | `AutomationContext` |
| `packages/core/terminal/__init__.py` | 公共导出（无副作用导入） |

**不在本交付**（其他 TA 的保留文件，勿与本核心混写的假设）：
`backend.py` / `spawn_win.py` / `guard.py` / `identity.py` / `win_pipe.py` /
`secret_store.py` / `ipc.py` / `runner.py` / `service.py` / `emulator.py`。

测试：`tests/test_terminal_{output,runtime,registry,lease,ownership,driver}.py`。

---

## 1. contracts.py：跨层共享的冻结类型

### 1.1 常量

| 名称 | 值 | 说明 |
| --- | --- | --- |
| `TERMINAL_ID_PREFIX` | `"term_"` | 命名空间分离 |
| `DEFAULT_OUTPUT_LOG_BYTES` | `262144` | 客户端游标窗口（计划 §13） |
| `DEFAULT_READ_SIZE` | `65536` | 单次 `read(size)` |
| `DEFAULT_EOF_GRACE_SECONDS` | `8.0` | 进程退出后的通道 EOF 有界宽限 |
| `DEFAULT_LEASE_GRACE_SECONDS` | `2.0` | lease 统一死期（心跳 1s） |
| `DEFAULT_STOP_CONFIRM_SECONDS` | `5.0` | shutdown 等 runner 整树退出 |
| `LEASE_ROLE_CONTROL` / `LEASE_ROLE_OBSERVER` | `"control"` / `"observer"` | token role |

### 1.2 枚举

- `RuntimeState`：`created / starting / running / exiting / exited / cleanup-failed / lost`
- `DrainStopReason`：`unknown / eof / cancelled / channel-error / eof-timeout / stop-requested`
- `IdentityCheck`：`not-required / verified / mismatch / probe-failed`
- `TerminateOutcome`：`not-attempted / returned / error / timed-out / refused-identity-mismatch`
- `OwnershipMode`：`service / detached / external`
- `Fidelity`：`full / partial / unavailable`
- `Recovery`：`full / partial / degraded / none`

### 1.3 异常（后续层稳定捕获；基类与原型一致）

| 异常 | 触发点 |
| --- | --- |
| `BackendUnavailableError(RuntimeError)` | 平台后端缺依赖/不支持（构造时解释，不阻断整个 Pan） |
| `InvalidCursorError(ValueError)` | 游标为负 / 超过 `total_bytes` |
| `IllegalStateTransition(RuntimeError)` | 状态机非法迁移；非运行状态 write/detach |
| `UnknownTerminalError(KeyError)` | registry / attachment 层 terminal 不存在 |
| `StaleLeaseError(RuntimeError)` | token 已撤销 / 世代过期 |
| `NotControlLeaseError(RuntimeError)` | observer 越权 / 伪造控制权 token |
| `OwnershipPolicyRequired(RuntimeError)` | `build_runtime(ownership=None)` |
| `UnownedTreeRejected(RuntimeError)` | 声明树守卫但实现为 None 且未显式确认 |
| `OwnershipGateError(RuntimeError)` | 启动证据四要素缺一 / 身份冲突 |
| `DetachUnsupportedError(RuntimeError)` | 无真实宿主能力时 detach（**取代**原型 `DetachedOwnershipNotImplemented`） |
| `ExternalOwnershipNotYetValidated(RuntimeError)` | `mode=EXTERNAL`（语义未定义） |
| `RegistryError` / `RegistryLockTimeout` / `RegistryCorruptError` | registry 持久化层 |
| `TerminalExistsError(ValueError)` | `create` 重复 id |

### 1.4 数据类（`as_dict()` 均为 JSON 安全）

- `ProcessIdentity(pid, created_at_filetime=None, created_at=None, image=None)`
  - `matches(other, *, tolerance=0.0)`：FILETIME 精确比较；只有 `created_at` 时给容差；
    无可比字段一律 False（fail-closed）。
- `ProcessOwnershipEvidence(assigned, atomic_with_spawn, identity, handle_bound_for_cleanup, guard, detail="")`
- `ExitInfo`：`code / process_exit_seen / reader_done / channel_eof / output_complete /
  drain_stop_reason / reason / observed_at / channel_error / bytes_at_stop`；
  `termination_complete` 仅当真实 EOF。**四个事实分开**，不可互相充当证据。
- `CleanupReport`：`interrupt_sent / terminate_result(TerminateOutcome) / terminate_error /
  tree_owned_pids / tree_remaining_pids / backend_closed / reader_joined / reader_converged /
  reader_cancelled / cancel_kind / drain_stop_reason / identity_check / state_after /
  owner_retained / error / seconds`；`ok` 只表示**清理**完整成功（与输出完整无关）。
- `OutputChunk(seq, data)` / `OutputPage(chunks, first_seq, next_cursor, gap, truncated)`
- `LeaseToken(terminal_id, client_id, generation, role, rows, cols, revocation_id)`
- `TerminalScope(workspace_id, session_id)`、`TerminalRecord`（见 §6.2）
- `ScreenSnapshot` / `WaitOutcome`（观察者层）
- `AppliedSnapshot(serialized_screen, cursor, rows, cols, fidelity, recovery, feed_lag, engine, note)`
  ——`cursor` = **已解析位置**（见 §4.5）。
- `DetachReport(terminal_id, detached_at, durable_owner, reconnect_hint, note)`
- `OwnershipPolicy(mode, lifecycle_owner, tree_guard_kind, tree_guard=None,
  lease_grace_seconds=None, detached=False, detach_handler=None, notes="")`
  - `describe()` / `on_service_shutdown(runtime)` / `reconnect_hint(terminal_id)`
  - `lifecycle_owner`（如 `"pan-service" | "runner" | "external-host"`）是**纯数据**，
    核心不按其分支（布局 A/B/C 都可用同一接口表达）。

类型别名：`IdentityProbe = Callable[[int], ProcessIdentity | None]`、
`OutputConsumer = Callable[[bytes], None]`、`TerminalLookup = Callable[[str], TerminalChannel | None]`。

### 1.5 Protocol（后续 TA 的实现目标）

```python
class PtyBackend(Protocol):              # TA-B backend.py 实现
    pid: int | None
    def read(self, size: int) -> bytes: ...   # 阻塞；对端结束抛 EOFError
    def write(self, data: bytes) -> int: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def alive(self) -> bool: ...
    def exit_code(self) -> int | None: ...
    def terminate(self, force: bool) -> None: ...
    def close(self) -> None: ...              # 句柄关闭（pywinpty 上会终止进程）

class TreeGuard(Protocol):               # TA-B guard.py 实现（JobObjectGuard）
    def describe(self) -> dict: ...           # 必须如实标 os_level_guard
    def owned_pids(self, root_pid) -> list[int]: ...
    def remaining(self, pids) -> list[int]: ...
    def terminate_tree(self, root_pid, *, timeout: float) -> tuple[list[int], list[int]]: ...

class StartupOwnershipGate(Protocol):    # ownership.UnverifiedOwnershipGate 已实现
    def verify(self) -> None: ...

class ScreenObserver(Protocol):          # observer.PyteScreenObserver 为 partial 参考实现
    def feed(self, data: bytes) -> None: ...
    def snapshot(self) -> ScreenSnapshot: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def wait_for(self, predicate, timeout, *, quiet_ms=0.0) -> WaitOutcome: ...

class AuthoritativeEmulator(Protocol):   # TA-B emulator.py 实现；本核心只声明
    @property
    def feed_lag(self) -> bool: ...
    def feed(self, data: bytes) -> None: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def snapshot(self, *, timeout: float = 2.0) -> AppliedSnapshot: ...
    def reset_baseline(self) -> AppliedSnapshot: ...

class DetachHandler(Protocol):           # 真实宿主能力；TA-B/C 注入
    def request_detach(self, runtime) -> DetachReport: ...

class TerminalChannel(Protocol):         # lease 目标（PtyRuntime 满足）
    def write(self, data: bytes) -> int: ...
    def resize(self, rows: int, cols: int) -> bool: ...

class AutomationDriver(Protocol):        # 业务 driver 层（不在核心）
    driver_id: str
    def run(self, context) -> dict: ...
```

---

## 2. output.py：`OutputLog`

```python
OutputLog(max_bytes: int = DEFAULT_OUTPUT_LOG_BYTES)
  .append(data: bytes) -> int            # 返回首字节绝对偏移；空 data 为 no-op
  .read_from(cursor: int, *, max_bytes: int | None = None) -> OutputPage
  .total_bytes / .retained_bytes / .dropped_bytes / .first_retained_seq
```

- 序号是**绝对字节偏移**（跨重连可复用）；`retained_bytes <= max_bytes` 是不变量；
- 驱逐按**整块**；单块超上限保留尾部并将头部计入 dropped；
- `cursor < first_retained_seq` -> `gap=(cursor, first_retained_seq)`，**不补零**，
  内容从保留窗口起点开始；窗口起点可能落在 UTF-8/CSI/OSC 内部——客户端必须走
  快照恢复，**不得**从窗口起点解析；
- `cursor > total_bytes` -> `InvalidCursorError`；非法游标绝不静默截断。

---

## 3. runtime.py：`PtyRuntime`

### 3.1 构造与工厂

生产装配**必须**走 `ownership.build_runtime`（fail-closed）；直接构造
`PtyRuntime(terminal_id, backend, *, output_cap, read_size, terminator, eof_grace,
identity, identity_probe, require_startup_gate, ownership, output_consumer)` 仅用于
测试/专用宿主。

### 3.2 状态机

```
CREATED ──> STARTING ──> RUNNING ──> EXITING ──> EXITED
   │                        │            └─────> CLEANUP_FAILED ──> EXITING（重试）
   └──> EXITING             └──> LOST
```

### 3.3 公开方法（返回与错误冻结）

| 方法 | 返回 | 错误/语义 |
| --- | --- | --- |
| `start(*, rows, cols, gate=None)` | `None` | 门禁不过 -> `OwnershipGateError`，状态保持 `created`、reader 不启动；gate 证据身份被采用 |
| `write(data)` | `int` | 非 running/exiting -> `IllegalStateTransition` |
| `resize(rows, cols)` | `bool` | 非运行返回 `False`（迟到 resize 不抛异常） |
| `read_from(cursor, *, max_bytes=None)` | `OutputPage` | 非法游标 -> `InvalidCursorError` |
| `poll_exit()` / `wait_exit(timeout)` | `ExitInfo` | 四个退出事实分开公布 |
| `wait_eof(timeout)` | `bool` | 只表示 reader 结束（原因看 `drain_stop_reason`） |
| `request_drain_stop()` | `None` | 下一循环检查点停止；不打断阻塞读（取消归 close） |
| `child_pids()` | `list[int]` | 由 `TreeGuard.owned_pids` 派生（不含根） |
| `close(*, reason, interrupt=True, interrupt_delay=0.15, terminate_timeout=1.5, tree_timeout=2.0, reader_grace=2.0, terminate=None)` | `CleanupReport` | 失败保留 owner；重复 close 幂等；`terminate=` 只用于测试注入 |
| `detach()` | `DetachReport` | 无宿主能力 -> `DetachUnsupportedError`；非 running -> `IllegalStateTransition`；不触碰 PTY |

属性：`state` / `detached` / `identity` / `ownership` / `log` / `rows` / `cols` /
`exit` / `consumer_errors`。

### 3.4 drain 契约（EOF ≠ alive）

- reader 只在 `read()` 抛 `EOFError`（或通道错误）时结束；`alive()=false` **不是**
  结束条件；空读且进程已退出进入有界 `eof_grace`（超时 -> `eof-timeout`）；
- `drain_stop_reason ∈ {eof, cancelled, channel-error, eof-timeout, stop-requested}`；
  **只有 `eof`** 置 `channel_eof=True` 与 `output_complete=True`；
- `output_consumer`（可选）：在同一 reader 临界区内与 OutputLog 追加后**同序**调用；
  必须快速返回；其异常不拖死 drain，记录到 `consumer_errors`。

### 3.5 close 顺序（失败保 owner）

```
1 身份核验（mismatch/probe-failed -> refused-identity-mismatch + cleanup-failed，拒杀）
2 所有权快照（owned_pids；必须在终止之前）
3 可选中断（Ctrl-C）
4 终止（有界；超时/错误记录 terminate_result）
5 整树终止 + remaining 核对（异常按 fail-closed 处理）
6 reader 收敛：终止+整树都成功才允许取消（关句柄）；取消无效 -> cleanup-failed
7 释放句柄（失败同样 cleanup-failed）
```

- 任何失败：`state=cleanup-failed`、`owner_retained=True`、记录不删、可重试；
- 取消原语（关句柄）在 pywinpty 会连带终止进程，因此**只在树确认死后**才取消；
- 成功才进入 `exited`；`drain_stop_reason=cancelled` 不阻止清理成功（`ok` 只描述清理）。

---

## 4. 给 TA-B（Windows backend / runner / emulator）的明确指引

### 4.1 `backend.py`：实现 `PtyBackend`

- `ConPtyBackend`（生产，自研 suspended spawn）与 `WinptyBackend`（参照回归，不进
  生产门禁路径）都实现 §1.5 协议；
- Windows 依赖（pywinpty/ctypes）**延迟导入**；缺失在构造时抛
  `BackendUnavailableError`（可执行安装提示），不得在模块导入期失败；
- `read` 必须可被 `close()` 打断（否则只能靠终止+整树成功后的取消路径）；
- `exit_code()` 未退出返回 `None`（不得用 259 判活）。

### 4.2 `guard.py` / `identity.py` / `spawn_win.py`

- `JobObjectGuard` 实现 `TreeGuard`；句柄生命周期由 runner 持有；
  `describe()["os_level_guard"]` 必须如实（psutil 兜底=False，只做测试观察）；
- `identity.py`：`IdentityProbe = (pid) -> ProcessIdentity | None`；
  **存活判定 = 同一 handle 上 `WaitForSingleObject(h, 0)`**（`WAIT_OBJECT_0`=已退出 /
  `WAIT_TIMEOUT`=存活）；FILETIME 取自 `GetProcessTimes`；`OpenProcess` 成功≠存活；
- `spawn_win.py` 的 `SpawnEvidence` 必须产出四要素，装配成
  `ProcessOwnershipEvidence` + `UnverifiedOwnershipGate`，作为
  `runtime.start(rows, cols, gate=...)` 的入参；**assign 失败/未原子入组一律拒绝
  running**（fail-closed）。

### 4.3 `emulator.py`：实现 `AuthoritativeEmulator`

- `snapshot(*, timeout)` 在 barrier 上返回 `AppliedSnapshot`；
  **`cursor` = 仿真器确认已解析应用的绝对字节位置（applied_seq）**，
  **禁止**用 producer 的 `total_bytes` 冒充；等待超时/`feed_lag` 时按
  `recovery=degraded|partial|none` 降级，禁止阻塞 reader、禁止静默全恢复；
- 供料接线：`build_runtime(..., output_consumer=emulator.feed)`——runtime 保证
  同序投递与异常隔离；有界队列/`feed_lag` 判定在 emulator 内实现；
- `reset_baseline()` 返回新基线快照（供 gap 后显式重打基线）。

### 4.4 `runner.py` 装配顺序（建议）

`spawn/attach → build_runtime(ownership=布局B策略, identity, identity_probe,
output_consumer=emulator.feed) → start(rows, cols, gate=Spawngate) → 服务循环 →
close()`；detach 走注入的 `DetachHandler`，不通过 `close`。

### 4.5 `service.py`（TA-C）装配

- `TerminalRegistry(root)` 只是**数据层**（§6）；运行时表 `{terminal_id: PtyRuntime}`
  由服务进程自持，`AttachmentRegistry(lookup=runtimes.get)` 接线；
- `reconnect_hint` / `on_service_shutdown` 用 `OwnershipPolicy` 的方法；
- 端口/Origin/CSRF 校验按计划 §6.3（不属于本核心）。

---

## 5. 所有权门禁：为什么 bool 不是保障

`UnverifiedOwnershipGate.verify()` 只校验证据**完整性**（四要素齐备）；证据真实性
必须来自真实 spawn 原语（挂起创建→assign→恢复、同一 handle 绑定）。本层不做、
也无法做 OS 级证明。`NullTreeGuard` 必须显式 `acknowledge_unowned_tree=True`，
并在证据中标注（`os_level_guard=False`）。

`build_runtime` 拒绝条件（任一即抛，绝不静默降级）：

| 条件 | 异常 |
| --- | --- |
| `ownership is None` | `OwnershipPolicyRequired` |
| `mode=EXTERNAL` | `ExternalOwnershipNotYetValidated` |
| `DETACHED`/`detached=True` 但无 `detach_handler` | `DetachUnsupportedError` |
| `tree_guard=None` 且未 `acknowledge_unowned_tree` | `UnownedTreeRejected` |
| 有树守卫但 `start()` 未给 gate | `OwnershipGateError` |

---

## 6. registry.py：`TerminalRegistry`

### 6.1 API

```python
TerminalRegistry(root=None, *, lock_timeout=5.0)   # root 默认 PAN_TERMINALS_DIR 或 data/terminals
  .new_terminal_id() -> str                        # term_ + uuid4 hex16（静态）
  .create(record) -> TerminalRecord                # 已存在 -> TerminalExistsError
  .save(record) -> TerminalRecord                  # upsert；刷新 updated_at
  .get(terminal_id) -> TerminalRecord              # 不存在 -> UnknownTerminalError
  .exists(terminal_id) -> bool
  .list() -> list[TerminalRecord]
  .remove(terminal_id) -> None                     # 仅 exited/lost；否则 IllegalStateTransition
```

- 原子写（tmp + `os.replace` + 有界重试）；跨进程锁（Windows 命名内核 mutex /
  POSIX flock）；损坏 JSON -> `RegistryCorruptError`（不静默丢弃）；
- 非法 id（路径穿越、非 `term_` 前缀）-> `ValueError`。

### 6.2 JSON schema（**无任何秘密/token 字段**）

```json
{
  "schema_version": 1, "terminal_id": "term_...", "owner": "service|detached",
  "status": "running", "created_at": 0.0, "updated_at": 0.0, "rows": 24, "cols": 80,
  "pid": 1234, "process_created_at_filetime": 134..., "pipe": "\\\\.\\pipe\\...",
  "detached": false, "detached_at": null,
  "scope": {"workspace_id": null, "session_id": null},
  "exit": {"code": null, "reason": null},
  "lease_grace_seconds": 2.0, "created_by": "mcp"
}
```

runtime 对象与 IPC 凭证**不在** registry；token 只允许存在于 DPAPI 秘密文件
（TA-B/C 实现），registry/日志/WS 一律不得出现。

---

## 7. attachments.py：`AttachmentRegistry`

```python
AttachmentRegistry(lookup: TerminalLookup)
  .attach(terminal_id, client_id, *, role="observer", rows=0, cols=0) -> LeaseToken
  .transfer_control(token, *, to_client) -> LeaseToken
  .detach(token) -> None         # 连接级撤销（≠ runtime.detach，永不触碰进程寿命）
  .is_revoked(token) -> bool
  .validate(token) -> None       # 观察者级校验（存在性+撤销）
  .send(token, data) -> int      # 需 control；校验+写同一临界区
  .resize(token, rows, cols) -> bool
  .control_holder(terminal_id) -> LeaseToken | None
```

- 撤销不可复活：`detach` 后该 token 与同代副本的 send/resize/transfer 全部
  `StaleLeaseError`；控制权撤销同时自增 generation；
- 伪造随机 `revocation_id` -> `NotControlLeaseError`；observer 越权 ->
  `NotControlLeaseError`；observer 不因控制权更替/撤销而误失效；
- 已知边界（如实声明）：复制 `revocation_id` 的 token 会被接受（M14.19）——
  授权必须由 Pan 入口在先完成；本层只解决进程内单 writer + 撤销语义。

---

## 8. observer.py / driver.py

- `PyteScreenObserver(rows, cols)`：延迟导入 pyte；`fidelity=partial`；
  `decode_modes` 还原 `mode<<5`；`SUPPORTS_ALTERNATE_SCREEN_BUFFER=False`、
  `SUPPORTS_SCROLLBACK=False`；`wait_for(predicate, timeout, *, quiet_ms)` 严格区分
  `matched` / `timed_out`。**不是**权威快照引擎；
- `AutomationContext(terminal_id, runtime, observer, control, attachments, timeout=30.0)`：
  `send_text`（UTF-8）/ `send_keys`（ASCII 键序列）/ `screen_text` / `wait_for`；
  无 `proc`、无生命周期 API；公共核心零 adapter 菜单字面量（有测试守护）。

---

## 9. 与契约原型（`pty_contract.py`）的差异（去探针化清单）

| 原型 | 正式核心 | 原因 |
| --- | --- | --- |
| 单文件 `pty_contract.py` | 拆为 9 个模块 + `contracts.py` 冻结区 | §8.5；backend 按冻结签名实现 |
| `TreeTerminator` | **`TreeGuard`**（同方法） | 与计划 §3.1 guard.py 对齐 |
| `DetachedOwnershipNotImplemented` | **`DetachUnsupportedError`** 且新增 `detach_handler` 能力位 | detach 需真实宿主能力，明确 Unsupported |
| `TerminalRegistry` 内存版（持有 runtime） | 文件持久化数据层（原子写+跨进程锁） | 计划 §3.1/§5.4 |
| `PsutilTreeTerminator` / `win32_identity_probe` / `psutil_identity_probe` | 不迁移（TA-B identity.py/guard.py） | 探针不生产化 |
| `tail_text_view` 反例工具 | 不迁移 | 探针工具 |
| `ScriptedBackend` | 移入测试（`test_terminal_runtime.py` 共享） | 测试设施不进入生产包 |
| `OwnershipPolicy` 无 detach 字段 | 新增 `detach_handler` | §runtime.detach |
| 无输出消费者接线 | `output_consumer` 注入（同序、异常隔离） | P1 emulator 供料接口 |
| `close()` 的 `terminate=` 注入 | 保留（测试专用；文档标注） | 失败路径测试必需 |

---

## 10. 测试矩阵（M1-M17 -> 正式 pytest）

| 契约组 | 测试文件 |
| --- | --- |
| M1-M3、M15（输出数学/边界） | `tests/test_terminal_output.py` |
| M4-M6、M12、M13（drain/状态机/清理/结束原因/reader 回收） | `tests/test_terminal_runtime.py` |
| M7（registry） | `tests/test_terminal_registry.py`（+ 跨进程竞争、无秘密、失败记录不删） |
| M8、M14（lease/撤销/churn） | `tests/test_terminal_lease.py`（+ 并发 churn） |
| M16、M17（所有权/门禁/身份） | `tests/test_terminal_ownership.py`（+ detach 能力位） |
| M9-M11（命名空间/driver 边界/pyte） | `tests/test_terminal_driver.py` |

运行命令（隔离环境，不改锁文件/全局环境）：

```bash
uv run --no-project --python E:/software/miniforge/python.exe \
  --with-requirements minimal-requirements.txt \
  --with pytest --with pytest-timeout --with pyte==0.8.2 \
  -- python -m pytest tests/test_terminal_output.py tests/test_terminal_runtime.py \
     tests/test_terminal_registry.py tests/test_terminal_lease.py \
     tests/test_terminal_ownership.py tests/test_terminal_driver.py -q
```

---

## 11. 已知边界 / 未测项（不得当作已解决）

1. **本核心是纯逻辑层**：真实 ConPTY/Job Object/管道/DACL/DPAPI 均未实现（TA-B/C）；
2. 启动门禁只校验证据完整性；**原子 spawn 证据**必须由 TA-B 的真实实现提供；
3. `runtime.detach()` 依赖注入的 `DetachHandler`；核心不提供任何宿主实现；
4. 权威快照（headless xterm sidecar）未验证：`fidelity/recovery=full` 在 P1 spike
   通过前不得对外宣称；
5. POSIX 只有接口与 flock 降级路径；PTY 后端未实现；
6. 跨进程 registry 竞争测试覆盖 4 进程 × 3 记录；未做长稳压测；
7. `AttachmentRegistry` 不做身份认证/网络授权；M14.19 已知边界如上。
