# Pan Terminal Runner 与 RunnerClient 接口冻结（P1，2026-10-03）

- 任务：T-TERMINAL-PTY-20261003 的 **P1 独立 Terminal runner 生产实现**。
- 工作树：`D:/project/pan-worktrees/terminal-runner-implement-20261003`
  （branch `implement/terminal-runner-20261003`，起点 `8af9b0f9`）。
- 性质：**接口冻结文档**。P2（服务层）按本文与
  `packages/core/terminal/runner.py` / `runner_client.py` 的签名接线；
  变更走记录，不得反向修改 `contracts.py` / `runtime.py` / `ipc.py` /
  `win_pipe.py` / `secret_store.py` / `backend.py`（本 TA 未改这些文件）。
- 输入（只读）：实施计划 §2/§5/§6/§8/§13、核心接口冻结（含 r2/r3）、
  IPC 接口（P1 传输/凭据）、backend 验收、IPC 验收。
- 本 TA 的**写范围**（其余文件只读）：

| 文件 | 内容 |
| --- | --- |
| `packages/core/terminal/runner.py` | runner 进程主体：bootstrap、FIRST pipe、HMAC 会话、布局 B 原子 owned runtime、lease watchdog、detach/durability、命令端点、worker 纪律、退出码 |
| `packages/core/terminal/runner_client.py` | P2 接线：`build_runner_argv` / `complete_bootstrap` / `RunnerClient`（attach + 8 个命令） |
| `tests/test_terminal_runner.py` | 隔离真机测试（Windows + 真实 ConPTY/Job/DPAPI/命名管道） |
| `docs/design/PAN_TERMINAL_RUNNER_INTERFACES_20261003.md` | 本文档 |
| `audit/terminal/implementation/runner/**` | 证据（JSON + 脚本 + README），新目录，不覆盖旧证据 |

---

## 0. 已冻结的依赖面（本层只读使用）

- `contracts.py`：`PtyRuntime` 语义、`OwnershipPolicy`、`ProcessIdentity`、
  `AppliedSnapshot`、枚举与异常；`OutputConsumer = (seq, data)`（第一参数为
  **该块首字节绝对偏移**）。
- `runtime.py`：`build_runtime`（fail-closed 工厂）、`PtyRuntime.start(rows, cols, gate=)`、
  `write/resize/read_from/poll_exit/detach/close`、输入接纳门、r3 有界清理。
- `backend.py`：`ConPtyBackend.spawn(argv, cwd, env, rows, cols, guard=None)`
  （挂起创建 → assign → resume；四要素证据）、`backend.probe(pid)`（DEAD 只来自
  同一 retained handle）、`backend.gate`、`backend.guard`。
- `ipc.py`：帧 schema（op 白名单 **read/input/snapshot/resize/lease/stop**）、
  challenge–hello–ack 双向 HMAC、`RequestScheduler` + `ClaimedRequest`
  （入队固化期限，不允许迟到执行）、`run_handler`（唯一 handler 调用点，
  认证 + schema + 类型 + `terminal_id` 绑定 + 期限复核）、精确整数
  （64 位值线上十进制字符串）。
- `win_pipe.py`：`PipeServer`（`FILE_FLAG_FIRST_PIPE_INSTANCE` + owner-only DACL）、
  `PipeConnection`、`PipeClient`、`current_process_identity`、`probe_process`、
  `terminate_verified_process`、`default_identity_probe`。
- `secret_store.py`：DPAPI 用户作用域秘密文件、`write_bootstrap_identity` /
  `wait_for_secret` / `verify_runner_identity` / `wait_for_bootstrap_identity`
  （默认强制核验）/ `update_runner_identity` / `delete_secret`（删除约束）。

依赖方向（无环）：`contracts/output/runtime/ownership/backend/guard/identity/
spawn_win/ipc/win_pipe/secret_store` ← `runner.py`；`runner_client.py` 只依赖
`ipc/win_pipe/secret_store/contracts`（**不**依赖 `runner.py` 的进程内对象）。

---

## 1. 进程与信任模型（布局 B）

```
Pan 服务（client，P2）                          runner（server，每终端一个进程）
  build_runner_argv(terminal_id, secret_file)     argv 只有 --terminal-id / --secret-file
  spawn（DETACHED_PROCESS；无 token argv/env）    ← 出生：写 hello（自身 pid + raw64 FILETIME）
  SecretStore.wait_for_bootstrap_identity        → 内核核验 ALIVE + pid/FILETIME 精确匹配
  → 生成 token → write_secret（DPAPI）           → wait_for_secret → verify_runner_identity（自检）
                                                  → env token 泄漏自检（fail-closed）
  PipeClient.connect()                           → PipeServer.create()（FIRST_PIPE_INSTANCE，占名拒绝）
  IpcSession(client).handshake()                 → IpcSession(server).handshake()（HMAC 双向，token 不上线）
  → lease(control) 心跳（1s）                    → 原子 spawn PTY + guard → gate 四要素 → RUNNING
  → read/input/resize/snapshot/stop              → 命令端点（handler）
```

- runner 是 PTY 整树的**唯一属主**：`ConPtyBackend.spawn(guard=None)` 自持
  `JobObjectGuard`（`KILL_ON_JOB_CLOSE`）罩住 shell 整树；runner 自身不进任何
  服务级 kill-on-close Job（除宿主 ambient Job 外，见 §6）。
- **公开 registry 不存秘密**：runner 不写 registry；token 只存在于 DPAPI 秘密文件
  与进程内存。argv/env/日志/响应体一律无 token。
- `terminal_id` 只决定对象名与管道名；授权 = DPAPI token（HMAC）+ 对端身份核验。
  **知道 terminal_id ≠ 权限**（服务层入口检查属 P2）。
- 同用户边界（如实声明）：DPAPI 用户作用域 + owner-only DACL 只隔离其它 Windows
  用户；同用户进程理论上可解密秘密/连接管道；首版信任边界 = 同一 Pan 用户。

---

## 2. bootstrap 顺序（冻结）

1. **argv**：`python -m packages.core.terminal.runner --terminal-id <term_...>
   --secret-file <path>`（可选 `--rows/--cols`）。argv **不含** token、不含祖先
   进程 PID；`--secret-file` 必须形如 `<root>/secrets/<terminal_id>.secret`
   （否则 fail-closed 退出，见 §7 退出码 4）。
2. **runner 自证**：`SecretStore.write_bootstrap_identity(terminal_id)` 写
   `<secret-file>.hello`，内容 = **自身** `pid + raw64 FILETIME`
   （`GetProcessTimes`，非 launcher/Popen PID）。
3. **等待秘密**：`wait_for_secret(timeout=10s)`；超时非零退出（期间**不创建 pipe、
   不接受连接**）。
4. **调用者核验**（P2 侧，`runner_client.complete_bootstrap`）：
   `wait_for_bootstrap_identity(verify=True)`（内核探针 ALIVE + pid/FILETIME 精确
   匹配）→ 生成 `token=secrets.token_hex(32)` → `write_secret`。
5. **runner 自检**：`verify_runner_identity(自身身份)`（不匹配 → fail-closed：
   删除自己未完成的 hello、非零退出、不监听）。
6. **token 泄漏自检**：扫描自身环境变量，token 出现即 fail-closed 退出（4）。
7. **FIRST 管道**：`PipeServer.create()`（`FILE_FLAG_FIRST_PIPE_INSTANCE`；
   名字被占 → `PipeBusyError` → 非零退出，绝不共用同名管道）。
8. **HMAC 会话**：接受连接 → `IpcSession(role="server")` 握手（challenge→hello→ack；
   服务器声明自身身份；`hmac.compare_digest`）。
9. **原子 owned runtime**：`ConPtyBackend.spawn(shell_argv, rows, cols)`（挂起创建 →
   assign → resume）→ `build_runtime(..., identity=backend.identity,
   identity_probe=backend.probe, output_consumer=emulator_bridge_or_None, ownership=布局B策略)`
   → `runtime.start(gate=backend.gate)`。
   四要素（`assigned` / `atomic_with_spawn` / `identity` / `handle_bound_for_cleanup`）
   缺一 → `OwnershipGateError` → 清理自有后代后非零退出。**没有证据不发布 running**：
   `describe().runner_state` 在 `runtime.start` 成功前保持 `starting`。

命令端点在步骤 9 完成后才报告 `running`；`read/input/resize/snapshot` 在
`starting`/失败状态下返回显式 `status`（不假装成功）。

---

## 3. 线上命令映射（冻结；含**接口矛盾报告**）

### 3.1 已冻结的 op 白名单（`ipc.py`，本 TA 不改）

请求 `payload.op ∈ {read, input, snapshot, resize, lease, stop}`；响应负载字段白名单
`{data_b64, seq, size, cursor, next_cursor, gap, truncated, rows, cols, status, detail,
snapshot, reason, total_bytes, first_retained_seq, ok}`；`status/detail/snapshot/reason`
≤ 4096 字符；`rows/cols ∈ [1,512]`。

### 3.2 矛盾报告（如实记录，不偷改共享文件）

| # | 矛盾 | 事实 | 本层处置 |
| --- | --- | --- | --- |
| I-1 | 任务要求端点实现 `describe/close/detach/owner-heartbeat` 独立命令，但冻结 op 白名单只有 6 个 op，**无法为一个新命令新增 op**（`validate_message` 在传输边界拒绝未知 op，`win_pipe.recv_frame` 也走该校验） | `ipc.py` 属 P1 IPC TA 的冻结写范围；本 TA 只读 | 用**已冻结 op 的组合**承载：`owner-heartbeat → lease`；`close → stop`；`detach → stop(reason="detach")`（reason 是 ≤64 字符自由串，作为**生命周期命令词表**）；`describe → read` 探测（见 I-3）。P2 若需独立 op，应由 ipc.py 所有者扩展协议版本后再放开 |
| I-2 | 快照协议 A 需要携带 serialized 屏幕，但响应字段 `snapshot` 上限 4096 字符（24×80 屏幕 + SGR 可能超界） | 白名单只约束单字段 | 用 `data_b64`（≤128 KiB 原始字节，base64 后 ≤ 白名单上界）+ `cursor` + `rows/cols` + `detail`（元数据 JSON）承载协议 A；`snapshot` 字符串字段不使用 |
| I-3 | `describe` 无独立 op；响应白名单没有 pid/filetime/detached/durability 等字段 | 同上 | **所有响应**统一携带 `status` + `detail`（有界 JSON 摘要，含 pid/FILETIME/rows/cols/detached/durability/lease/exit/worker 状态）；`describe` 命令 = 一次 `read(cursor=0, max_bytes=1)` 探测（非 mutating、无 barrier、快速），从 `detail` 解析 |
| I-4 | 任务要求"只有可信 Pan 所有者心跳续 lease"，而 `lease` op 的 payload（client_id/role/generation）本意是 attachment lease | 白名单固定 | runner 把 `lease` 解释为 **IPC 所有者租约**（与 `attachments.py` 的浏览器控制权 lease 是两套凭据，见 P1 IPC 文档 §1）：`role=control` 续/接管租约；`role=observer` 只登记连接，**不改变 runtime、不续约** |

以上四条已同步给 P2/MA 的接线依据；本层不做协议版本升级。

### 3.3 命令语义（端点层）

| 命令 | 线上请求 | 响应要点 |
| --- | --- | --- |
| `describe` | `read{cursor:0,max_bytes:1}` | `status`=runner_state；`detail`=描述 JSON（§4.4） |
| `read(cursor,max_bytes)` | `read` | `data_b64`（单块拼接，绝对偏移）`seq` `next_cursor` `gap` `truncated` `total_bytes` `first_retained_seq` + 统一 `status/detail` |
| `input(data,seq?)` | `input{data_b64,seq?}` | 有界等待写 worker：`status ∈ accepted/done/busy/rejected`、`size`=已写字节（完成时） |
| `resize(rows,cols)` | `resize{rows,cols}` | `rows/cols`；`detail.emulator_resize ∈ ok/none/error:<type>`；**不声明**物理尺寸已同步 |
| `snapshot(timeout_ms)` | `snapshot{timeout_ms}` | `data_b64`=serialized 屏幕（UTF-8）、`cursor`=applied/parsed 绝对偏移；`detail` 含 fidelity/recovery/feed_lag/engine/note；无引擎时 `fidelity=unavailable,recovery=none` |
| `owner-heartbeat` | `lease{client_id,role:"control",generation?}` | `detail.owner/generation/detached`；续约 = 更新 `last_heartbeat` |
| `observer-register` | `lease{client_id,role:"observer"}` | 仅登记；**不**续约、**不**改变 runtime |
| `detach` | `stop{reason:"detach"}` | 能力允许 → `status=detached`（durable，lease 死期不再杀）；受 ambient Job 限制 → `status=detach-refused`（显式拒绝，零状态变化） |
| `close` | `stop{reason:"<close|explicit-close|service-shutdown|...>"}` | 关闭 worker 有界等待：`status ∈ exited/closing/cleanup-failed`；失败保 owner、可重试（同一 worker 复用） |

- 所有请求经 `RequestScheduler`：**入队固化期限**（`min(sender_deadline, 入队时刻+timeout_ms)`），
  过期 → 零 handler 调用、回 `expired`；队列满 → 拒绝（不阻塞、不无界增长）。
- `terminal_id` 严格绑定：与会话不一致 → `terminal-mismatch`，零 handler 调用。
- mutating（`input/resize/lease/stop`）超时后结果未知：客户端**禁止静默重试**，
  应重新 `describe`/`snapshot` 对齐（`RunnerClient` 文档标注）。

---

## 4. runner 状态、worker 纪律与快照

### 4.1 lease（统一口径）

| 参数 | 默认 | 语义 |
| --- | --- | --- |
| 心跳间隔（P2 责任） | 1 s | P2 每 1s 发 `lease(control)` |
| 统一死期 | 2 s（`DEFAULT_LEASE_GRACE_SECONDS`） | `now - last_heartbeat ≥ 2s` → 判丢失 → 自停整树 |
| owner 缺席宽限 | 10 s（`bootstrap_grace_seconds`） | 首个 owner 心跳到达前不计死期；超时按丢失自停（Pan 崩溃但未连接） |
| owner 接管 | 任意已认证 `control` 心跳 | `client_id` 变化即接管，`generation` 自增（或采用请求值） |
| observer/浏览器 | 无影响 | observer 连接/断开**不改变 runtime、不续约**（浏览器从不直接连 runner；P2 只是转发） |

- **只有已认证连接**（DPAPI token + 双向 HMAC）能续约；未认证帧在认证前不可达。
- 死期触发 `close(reason="lease-expired")`；清理未收敛时有界重试（默认 3 次），
  最后一次仍失败 → 记录 `cleanup-failed` 后退出（退出即关闭 guard 句柄 = 内核清整树，
  **如实标注为内核兜底而非已确认清理**）。
- `detached=True` 后 lease 死期**不再**触发关闭；新 controller 重连后恢复心跳。

### 4.2 worker 纪律（冻结）

- **write/close 不在 IPC/lease 循环内直接执行**：每次调用交给可追踪 worker 线程；
  调用方（连接线程/看门狗）只做**有界等待**。
- 同一时刻**最多一个在途 write worker**：未完成期间新 `input` → `busy`（显式拒绝，
  不排队、不叠加、不重复启动同一阻塞调用）；完成后才允许下一次。
- **close worker 跨重试复用**（不重叠）；超时/未完成时响应 `closing`，
  **不释放 owner、不谎报 exited**；重试收敛后才 `exited`。
- close 开始时关闭 runner 输入门：此后新 `input` 立即 `rejected`（不触达 runtime）。
- 看门狗线程独立：只读 lease 时间戳 + 触发 close worker；被执行中的 handler
  阻塞不会拖死它（每个连接独立线程；handler 有界等待 ≤ 1s，见 §4.3）。
- 清理报告只带**结构化字段**（state_after / terminate_result / 残留数 /
  identity_check / ok）；诊断文本只允许静态串与类型名，不落异常消息（防秘密泄漏）。
- `write_hook`（构造参数）**仅测试/诊断**：生产不注入（默认 `runtime.write`）。
  本机实测：ConPTY 不会因输入量阻塞（2.7MB 输入被吸收），因此"阻塞写"的行为验证
  由注入门在真实 runner 进程的 write worker 内完成（证据见 `audit/.../runner/`）。

### 4.3 有界等待（默认）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `input_ack_wait` | 0.75 s | `input` handler 等待 write worker 的上限；超时回 `accepted-in-flight`（心跳不被拖过死期：0.75s < 2s） |
| `write_budget`（backend 内） | 1.5 s | 单次写总预算（backend 契约），完成判定在 worker 内 |
| `close_wait` | 8.0 s | `stop` handler 等待 close worker 的上限；超时回 `closing` |
| `lease_cleanup_retries` | 3 | 死期清理未收敛时的有界重试次数（0.5s 间隔） |
| `stop_confirm`（P2 侧） | 5 s | 计划 §13：服务等 runner 整树退出（P2 实现，本层提供 `close` 语义） |

- **慢操作与 lease 的交互（如实声明）**：续约只由**已处理的** `lease` 请求承担。
  同一连接上若有长 handler（如 `snapshot` barrier 取客户端 `timeout_ms`），其后的
  心跳会被延后；单连接且延后 ≥ 死期（2s）时 runner 按死期自停——这是"只有可信
  所有者心跳续 lease"的直接后果。P2 若需要长 barrier，应在**独立连接**上心跳或
  使用短超时；`input` handler 已内置 0.75s 上限以避免该问题。

### 4.4 `detail` 摘要（describe JSON，所有响应统一携带）

```json
{
  "runner_state": "starting|running|detached|closing|exited|cleanup-failed",
  "runtime_state": "running|exiting|exited|cleanup-failed|lost",
  "pid": 1234, "process_created_at_filetime": "134...",   // raw64 线上十进制字符串
  "rows": 24, "cols": 80,
  "detached": false,
  "durability": {"capable": true, "ambient_job": false, "detail": "..."},
  "lease": {"established": true, "owner": "pan-1", "generation": 1, "age_ms": 120, "grace_ms": 2000},
  "exit": {"seen": false, "code": null, "reason": "..."},
  "cleanup": {"state_after": "exited", "ok": true, "seconds": 0.3},
  "consumer": {"failed": false, "count": 0, "gap_seen": false},
  "snapshot": {"engine": "injected|none", "fidelity": "...", "recovery": "...", "feed_lag": false},
  "input_worker": {"in_flight": false, "completed": 3, "last_status": "done"},
  "close_worker": {"in_flight": false},
  "connections": {"accepted": 2, "active": 1}
}
```

### 4.5 快照协议 A（冻结）

- 线上：`data_b64`（serialized_screen UTF-8 字节）+ `cursor`（**仿真器已解析/应用**的
  绝对字节位置，非 producer `total_bytes`）+ `rows/cols` + `detail`（fidelity/recovery/
  feed_lag/engine/note）。
- 客户端：清屏 → 应用 serialized → **从 `cursor` 续读原始流**（`read(cursor=...)`）；
  **不额外喂 pending_tail**，不从窗口起点解析。
- 无引擎：`fidelity=unavailable,recovery=none,serialized=""`；**不承诺完整恢复**。
- 桥接降级（粘滞）：供料缺口（`seq` 不连续，无 `feed_at`）、`feed_lag`、
  消费者异常任一发生 → 快照 `recovery ≥ degraded` 且 note 记录原因；**禁止**在
  这些条件下返回 `recovery=full`。

### 4.6 仿真器桥接（`AuthoritativeEmulator` → `OutputConsumer`）

- runner 接受注入的 `AuthoritativeEmulator`（本 TA 不实现引擎；emulator.py 属并行 TA）。
- 桥接器 `RunnerEmulatorBridge(seq, data)`：
  - 首次 feed 以 `seq` 建立期望位置；连续时调用 `feed_at(seq, data)`（若仿真器提供该
    **可选扩展**）否则 `feed(data)`；
  - 不连续（缺口）→ 粘滞 `gap_seen` + 有界 gap 记录（不丢失缺口事实）；有 `feed_at`
    时按真实偏移投递，否则仍 `feed(data)` 但**永久降级**（快照不允许 full）；
  - 桥接异常由 runtime 的 `output_consumer` 隔离（只记类型名）；桥接自身不抛。
- resize 与 feed 走**同一有序通道**：runner 调用 `emulator.resize(rows, cols)`；
  次序由仿真器内部通道保证（协议要求）；runner 不绕过、也不声称 PTY 与仿真器
  尺寸已同步（`detail` 如实分列）。

---

## 5. 生命周期命令与退出码（冻结）

| 事件 | runner 行为 | 退出码 |
| --- | --- | --- |
| `stop`（close/service-shutdown/lease-expired） | close worker：身份核验→所有权快照→中断→终止→整树→reader 收敛→句柄；成功 → 退出 | 0 |
| 死期清理有界重试后仍未收敛 | 记录 `cleanup-failed`，退出（内核 guard 兜底） | 3 |
| bootstrap/身份/管道/门禁失败（含 token 入 env） | fail-closed，删除未完成 hello，不监听 | 4 |
| 正常运行中（含 detach 后） | 服务循环；不自行退出 | — |

- **Ctrl-C 不是已工作**：close 的中断只是 best-effort（`runtime.close(interrupt=True)`）；
  完成与否只看终止 + 整树 + 根确认 + reader 收敛的证据（CleanupReport）。
- **不以 kill 替代 interrupt**：不把 `TerminateProcess` 当成优雅中断的替代品；
  顺序由 runtime 契约固定。
- `detach` 后不随 Pan 正常关闭/崩溃退出；显式 `stop` 仍可终止（detached 也接受）。

---

## 6. durability 能力与 ambient Job（冻结）

- runner 出生自检 `IsProcessInJob(GetCurrentProcess(), NULL)`：为真 = 处于**宿主
  ambient Job**（本进程从不把自己 assign 进自有 guard Job）。
- 宿主 Job 可能带 `KILL_ON_JOB_CLOSE`：Pan 退出 → Job 句柄关闭 → runner 被杀 →
  **detach 的 durable 承诺不成立**。该限制无法在进程内清除（无 Job 句柄、无法查询）。
- 因此 `describe().durability.capable` 如实上报；**受限制时 `detach` 显式拒绝**
  （`status=detach-refused`，零状态变化），绝不假成功。
- P2 应在 `detach` 前检查能力（或用响应判断）；约束存在时向用户明确说明
  "该环境不支持 durable detach"。
- 真实环境确证（本机 2026-10-03，见审计证据）：宿主 ambient Job 存在且
  `CREATE_BREAKAWAY_FROM_JOB` 被拒（WinError 5）→ 无法清除限制 → 按上述 fail-closed。

---

## 7. 公共 API（冻结）

### 7.1 `runner.py`

```python
RUNNER_EXIT_OK = 0
RUNNER_EXIT_CLEANUP_FAILED = 3
RUNNER_EXIT_BOOTSTRAP_FAILED = 4
RUNNER_EXIT_USAGE = 2

class RunnerBootstrapError(RuntimeError): ...

@dataclass(frozen=True)
class DurabilityCapability:
    durable_capable: bool
    ambient_job: bool
    detail: str

def detect_ambient_job() -> bool
def detect_durability_capability() -> DurabilityCapability

class RunnerEmulatorBridge:            # OutputConsumer 实现（可单测）
    def __init__(self, emulator: AuthoritativeEmulator) -> None
    def __call__(self, seq: int, data: bytes) -> None
    @property
    def gap_seen(self) -> bool
    @property
    def gaps(self) -> tuple[tuple[int, int], ...]   # 有界
    @property
    def fed_through(self) -> int

class TerminalRunner:
    def __init__(self, terminal_id: str, secret_file: str | Path, *, rows=24, cols=80,
                 shell_argv: Sequence[str] | None = None, cwd: str | None = None,
                 lease_grace_seconds: float = 2.0, bootstrap_grace_seconds: float = 10.0,
                 bootstrap_timeout_seconds: float = 10.0,
                 input_ack_wait: float = 0.75, close_wait: float = 8.0,
                 lease_cleanup_retries: int = 3,
                 emulator: AuthoritativeEmulator | None = None,
                 durability_probe: Callable[[], DurabilityCapability] | None = None,
                 identity_probe: Callable[[int], ProcessProbe] | None = None,   # 默认 backend.probe
                 write_hook: Callable[[bytes], int] | None = None,             # 仅测试/诊断（默认 runtime.write）
                 accept_timeout: float = 0.5,
                 diagnostics: Callable[[dict], None] | None = None) -> None
    # 进程内命令端点（P2 通过 runner_client 走同一语义）
    def run(self) -> int                                  # 同步主流程，返回退出码
    def describe(self) -> dict[str, Any]
    def read(self, cursor: int, *, max_bytes: int | None = None) -> dict[str, Any]
    def input(self, data: bytes, *, seq: int | None = None) -> dict[str, Any]
    def resize(self, rows: int, cols: int) -> dict[str, Any]
    def snapshot(self, *, timeout_ms: int = 5000) -> dict[str, Any]
    def owner_heartbeat(self, client_id: str, *, generation: int | None = None) -> dict[str, Any]
    def register_observer(self, client_id: str) -> dict[str, Any]
    def detach(self, *, reason: str = "detach") -> dict[str, Any]
    def close(self, *, reason: str = "explicit-close") -> dict[str, Any]
    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]   # ipc 端点分发

def main(argv: Sequence[str] | None = None) -> int
```

约束：

- `run()` 顺序 = §2；`identity_probe` 默认 **`backend.probe`**（装配约束），
  注入仅用于测试/诊断（生产不传）。
- `durability_probe` 默认 `detect_durability_capability`。
- 所有端点方法返回 JSON 安全 payload（供 `ipc.build_response` 校验），字段受 §3.1
  白名单限制；数值型 raw64（FILETIME/cursor/seq/total_bytes）由 ipc 层编码为十进制字符串。
- `handle(request)` 是**唯一** IPC 分发点（`IpcSession.run_handler` 之内）。

### 7.2 `runner_client.py`

```python
class RunnerClientError(RuntimeError): ...
class RunnerSecretUnavailable(RunnerClientError): ...
class RunnerAttachError(RunnerClientError): ...

def build_runner_argv(terminal_id: str, secret_file: str | Path, *,
                      python: str | None = None, rows: int | None = None,
                      cols: int | None = None) -> list[str]

def complete_bootstrap(store: secret_store.SecretStore, terminal_id: str, *,
                       timeout: float = 10.0, token: str | None = None,
                       pipe_name: str | None = None,
                       poll: float = 0.05) -> secret_store.SecretPayload

class RunnerClient:
    def __init__(self, terminal_id: str, *, data_root: str | Path | None = None,
                 connect_timeout: float = 5.0,
                 request_timeout_ms: int = 10_000,
                 close_timeout_ms: int = 15_000,
                 transport: Any | None = None,          # 测试注入（跳过真实管道）
                 identity_probe: Callable[[int], Any] | None = None,
                 secret: secret_store.SecretPayload | None = None,
                 client_id: str | None = None) -> None
    def attach(self) -> "RunnerClient"          # 读秘密 → 连管道 → 双向握手
    @property
    def authenticated(self) -> bool
    @property
    def peer_pid(self) -> int | None
    @property
    def session(self) -> ipc.IpcSession        # 高级用法/协议自检（业务用具体方法）
    def call(self, op: str, payload: Mapping[str, Any] | None = None, *,
             timeout_ms: int | None = None, io_slack: float = 1.0) -> dict[str, Any]
    def describe(self) -> dict[str, Any]
    def read(self, cursor: int = 0, *, max_bytes: int | None = None) -> dict[str, Any]
    def input(self, data: bytes, *, seq: int | None = None,
              timeout_ms: int | None = None) -> dict[str, Any]
    def resize(self, rows: int, cols: int) -> dict[str, Any]
    def snapshot(self, *, timeout_ms: int = 5000) -> dict[str, Any]
    def heartbeat(self, *, generation: int | None = None) -> dict[str, Any]
    def register_observer(self) -> dict[str, Any]
    def detach(self) -> dict[str, Any]
    def close(self, *, reason: str = "explicit-close") -> dict[str, Any]
    def release_connection(self) -> None        # 断连只释放连接（不触碰 runtime）
```

- `attach()` 读 `SecretStore(data_root).read_secret(terminal_id)`，用
  `secret.runner_identity()` 作为 `expected_peer_identity`（**先核验服务器身份，
  再发 hello**）；token 只进入内存与 HMAC 域。
- `describe()` = `read(cursor=0, max_bytes=1)` 探测并解析 `detail`；
  `snapshot()` 解码 `data_b64` + `cursor` + 元数据。
- 客户端对 mutating 超时抛 `RequestTimeout`（不静默重试）；调用方应重新
  `describe()/snapshot()` 对齐。
- `release_connection()` 只关连接；**不**发送 stop、**不**删除秘密。

---

## 8. 测试与证据（本 TA）

- `tests/test_terminal_runner.py`（Windows 真机 + 隔离临时数据根）覆盖：
  管理心跳保活 / 死期 2s 自停；观察者轮换不杀 runtime；detach 语义
  （能力覆盖注入下的同 PID/FILETIME + shell 变量保存 + 新 controller 重连）与
  受 ambient 限制时的显式拒绝；根死孙活整树清理；runner 硬死 → guard 内核清整树；
  startup/身份/认证拒绝；cleanup-failed 保 owner 重试；阻塞 write 不堵 watchdog；
  跨进程 secret 重连；token 无 argv/env/日志泄漏。
- 证据：`audit/terminal/implementation/runner/`（pre-fix 失败日志、post-fix 全绿日志、
  机器可读 JSON、清理扫描、README）；不覆盖旧证据目录。

## 9. 已知边界 / 未验证（不得当作已解决）

1. **权威仿真器引擎未实现**：本层只提供桥接与降级语义；无引擎时快照
   `fidelity=unavailable`（不承诺恢复）。`emulator.py` 与 sidecar 属并行 TA。
2. **ambient Job 下的 durable detach 未实证**：本机宿主 Job 不可清除
   （breakaway 被拒），detach 语义在能力覆盖注入下验证，真实环境按 fail-closed 拒绝。
3. **Pan 服务/浏览器/Web/MCP/registry 接线未实现**（P2/P3）。
4. **跨用户/跨主机拒绝未实测**（同 P1 IPC 边界）；`PIPE_REJECT_REMOTE_CLIENTS`
   仍只有结构性证据。
5. **未做长稳压测**：lease 抖动、连接 churn、慢客户端背压只做有界重复；
   `stop` 超时后的迟响应语义依赖 ipc 的 `late_responses` 计数。
6. **旧 Windows build 的 ClosePseudoConsole/取消路径**未验证（沿用 backend 报告边界）。
