# Pan Terminal Runner 与 RunnerClient 接口冻结（P1，2026-10-03）

- 任务：T-TERMINAL-PTY-20261003 的 **P1 独立 Terminal runner 生产实现**。
- 工作树：`D:/project/pan-worktrees/terminal-runner-implement-20261003`
  （branch `implement/terminal-runner-20261003`，起点 `8af9b0f9`）。
- 版本记录：首版对应 `d48f967e`（独立审查固定快照，报告
  `PAN_TERMINAL_RUNNER_REVIEW_20261003.md` @ `a12a6bd6`，判定 **返工 Restricted**）；
  本文件为 **r2 返工后版本**（闭环 R1/R2/R3+O1 与 O2..O7，见 §10 变更记录）。
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
- **浏览器不得直连 runner、不得持有 token**：浏览器/观察者权限属于服务层
  attachment lease（`attachments.py`），由 P2 在入口执行授权与转发；runner 层的
  `observer` 只是**消息级登记**，不构成连接级角色隔离。任何持有 DPAPI token 的
  已认证连接都具备 input/stop/control 能力——这是**同用户信任模型内的设计事实**，
  不是远程漏洞；跨用户/跨主机拒绝未实测（沿用 P1 IPC 边界）。
- 若未来需要连接级角色强制，需协议版本升级（不在 P1 冻结 op 白名单能力内）。

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
   **仅当失败发生在自检之前/之中时**删除自己未完成的 hello；自检通过后的失败
   （pipe/门禁/运行期）**保留** hello，由 Pan 侧按事实回收——措辞口径见 §5）。
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

> 状态（2026-10-03 更新）：I-1..I-4 已由 MA **冻结为内部接线约束**
> （`PAN_TERMINAL_COMPOSITION_BOUNDARIES_20261003.md`），依据组合验证 `7e6b30cb` 与独立审查
> `cabacc7f`；**仍非 P2 批准/发布许可**，不升级 IPC 帧版本；`ipc.py` 白名单与共享契约未改，
> 独立 op 扩展仍属 ipc 所有者。

| # | 矛盾 | 事实 | 本层处置 |
| --- | --- | --- | --- |
| I-1 | 任务要求端点实现 `describe/close/detach/owner-heartbeat` 独立命令，但冻结 op 白名单只有 6 个 op，**无法为一个新命令新增 op**（`validate_message` 在传输边界拒绝未知 op，`win_pipe.recv_frame` 也走该校验） | `ipc.py` 属 P1 IPC TA 的冻结写范围；本 TA 只读 | 用**已冻结 op 的组合**承载：`owner-heartbeat → lease`；`close → stop`；`detach → stop(reason="detach")`（reason 是 ≤64 字符自由串，作为**生命周期命令词表**）；`describe → read` 探测（见 I-3）。词表冻结为 `close / explicit-close / service-shutdown / lease-expired / detach`（≤64 自由串，仅 `detach` 有语义特判；**不是**协议枚举校验），新增词需记录用途。**注入引擎生命周期归属**：由创建它的 runtime 宿主承担（生产应为独立 Runner 进程内的 launcher/bootstrap）；`TerminalRunner` **不隐式关闭借入对象**；宿主在 `runner.run()` 返回后以有界预算关闭引擎、保留失败 owner 并记录重试；Pan 服务只是 IPC 控制者，不得把常驻引擎放在依赖 Pan 服务生存的进程里（违背 detach 语义）。硬死 Job 清理只引用已测布局与所有权前提，不当作通用保证。**不新增独立 `shutdown` op** |
| I-2 | 快照协议 A 需要携带 serialized 屏幕，但响应字段 `snapshot` 上限 4096 字符（24×80 屏幕 + SGR 可能超界） | 白名单只约束单字段 | 用 `data_b64`（**128 KiB 是 serialized 原始字节上限**，非 base64 长度；base64 + 元数据合计仍须满足 **256 KiB 帧上限**）+ `cursor` + `rows/cols` + `detail`（元数据 JSON）承载协议 A；`snapshot` 字符串字段不使用。**边界条款（实测）**：OutputLog 驱逐后若 applied cursor 已被驱逐（`applied < first_retained`），`read(cursor)` 返回 gap → 协议 A 续流**仅在 cursor 未被驱逐时成立**；此时客户端走 fresh-view（显示层显式恢复策略，**不是**自动 `reset_baseline`，也不结束 PTY）或在引擎追平后取新快照重试 |
| I-3 | `describe` 无独立 op；响应白名单没有 pid/filetime/detached/durability 等字段 | 同上 | **所有响应**统一携带 `status` + `detail`（有界 JSON 摘要，含 pid/FILETIME/rows/cols/detached/durability/lease/lifecycle/exit/worker 状态）；`describe` 命令 = 一次 `read(cursor=0, max_bytes=1)` 探测（非 mutating、无 barrier、快速），从 `detail` 解析。**F5 已实施**：`snapshot` detail 增补 `cursors_valid` / `reset_unconfirmed`（来源已核验才 bool，缺失/异常 = null unknown；unknown/false/reset 未确认不得作为完整续流依据；缩减 detail 时仍保留）；**原因来源分层**：node 未验证序列原因只在 `snapshot.note`，`diagnostics().reasons` 是 Python 层集合、为空**不代表**无降级；不解析 note 伪造结构化 reasons API，不新增顶层白名单字段，不改共享 `AppliedSnapshot` |
| I-4 | 任务要求"只有可信 Pan 所有者心跳续 lease"，而 `lease` op 的 payload（client_id/role/generation）本意是 attachment lease | 白名单固定 | runner 把 `lease` 解释为 **IPC 所有者租约**（与 `attachments.py` 的浏览器控制权 lease 是两套凭据，见 P1 IPC 文档 §1）：`role=control` 续/接管租约；`role=observer` 只登记连接，**不改变 runtime、不续约**。冻结补充：浏览器永不直连 runner、不持 token；浏览器 attachment 与 owner heartbeat 是不同层；`lease(control)` 只由 Pan 服务使用；重连维持**稳定 client_id**（同 id 续约不增 generation，新 id 接管保持既有语义），稳定 id 不是权限证明；心跳使用独立连接，不受慢 snapshot/input/close 阻塞 |

以上四条为**冻结的内部接线约束**（对齐冻结文档各节）：I-1 词表/宿主归属、I-2 载荷与
缺口边界、I-3 确认字段来源分层、I-4 lease 与 attachment 分层；授权、身份与 Web/MCP 入口校验
仍由 P2 与 ipc 所有者决定。新增词/字段/结构需报备并在本表记录。

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
- **世代（epoch）与迟到 expiry（R2）**：每次已处理的续约/接管都自增 `_lease_epoch`；
  expiry 判定携带检测时刻的世代，在取得关闭权之前**复核**（世代未变 + 仍未续约才继续）。
  因此"迟到 expiry"不会误杀新 lease；检测后发生的任何续约都会作废旧判定。
- **N1 指令级线性化（`_acquire_lease_close_right`）**：expiry 的"复核 + 取得**不可撤销**
  关闭权（`_expiry_in_progress=True`）"在 **lease 锁内原子完成**，与 `owner_heartbeat`
  共用同一把锁，两条指令流严格线性化：
  - hb 先被接受（世代自增）→ expiry 的取得返回 `renewed`（旧 expiry 作废，不关树）；
  - expiry 先取得关闭权 → hb 在锁内看到该位，只能返回 **`closing`**（ok=False，
    **不得假 ok**、不续约、不接管）；关闭权不可撤销，关闭链继续（失败仍走 R1 同 worker
    消费/重试，输入门保持关闭）。
- **关闭权仲裁（R1/R2）**：`close(source="lease")` 在生命周期锁内复核 `detached`
  （detach 已完成 → `lease-skipped-detached`）；一旦 expiry 取得关闭权
  （`_expiry_in_progress`），`detach` **明确拒绝**（`status=detach-refused`，
  detail.reason=`lease-close-in-progress`），零状态变化；用户/服务**显式 close**
  （`source="explicit"`）不受跳过影响：detached 也照常终止。
- **显式 close 未收敛/失败后 Pan 断开（R1）**：`_closing` **不再**永久豁免 lease 检查。
  watchdog 仍负责：等待并**消费同一 close worker** 的结果（迟到成功 → 直接 `exited`，
  不重复发起同一阻塞调用）；失败 → 有界重试（仅当上一 worker 已结束才另起，不叠加）；
  耗尽 → 明确非零终态（退出码 3 + 状态文件）。输入门**不恢复**（`_closing` 一旦置位保持）。
- 死期清理重试耗尽 → 记录 `cleanup-failed` 后退出。退出会关闭 guard 句柄（KILL_ON_JOB_CLOSE），
  但**不得宣称该内核兜底已被普遍实测**：仅 r2 的特定场景做过端到端观测（见 §9 与审计 r2 证据）。
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
  阻塞不会拖死它。**等待分层（O5 修正）**：仅 `input` handler 有 0.75s 的
  ack 上限；`close` handler 等待 ≤ `close_wait`（默认 8s）；`snapshot` 等待由
  客户端 `timeout_ms` 决定（上限 60s，见 §4.3）。
- 清理报告只带**结构化字段**（state_after / terminate_result / 残留数 /
  identity_check / ok）；诊断文本只允许静态串与类型名，不落异常消息（防秘密泄漏）。
- `write_hook`（构造参数）**仅测试/诊断**：生产不注入（默认 `runtime.write`）。
  本机实测：ConPTY 不会因输入量阻塞（2.7MB 输入被吸收），因此"阻塞写"的行为验证
  由注入门在真实 runner 进程的 write worker 内完成（证据见 `audit/.../runner/`）。
- 收尾预算（R1/O3/N4）：`run()` finally 会 `_close_pipe_server()`（不收敛保引用）、
  以**共享总 deadline** join 连接线程（默认 3s，非 N×2s）、有界 join watchdog（3s），
  再由 `_finalize()` 在**从入口计时的总预算（12s）**内做最后一次关闭：
  - 预算**包含** lifecycle 锁的有界等待（`RLock.acquire(timeout=剩余)`）与 close worker
    等待；剩余预算逐段传递（`min(close_wait, remaining)`）；连接线程与 watchdog 的
    join 位于此前的独立 3s + 3s 分段，不计入 `_finalize` 的 12s。状态文件写入在
    finalize 之后，也不属于该预算；不能把 12s 当作整个 run 收尾的硬上界；
  - 锁忙（被其它关闭链持有）→ **不假 success、不丢 owner、不裸关在途句柄**：记录
    `finalize.outcome="lock-busy"` 与非零退出码（cleanup-failed），随后仍可重试；
  - 预算耗尽 → 不静默延长、不多次最低预算重试 → `budget-exhausted` + 未证明码（6）；
  - 在途 close worker 复用原 worker（不重叠）；`finalize` 结果（outcome/lock/worker
    追踪）进入状态文件 `cleanup.finalize`；
  - 该预算是**调用方侧有界等待**，**不是 OS 原语硬 SLA**（锁获取/GIL/调度只保证
    "尽力在预算内返回"）。

### 4.3 有界等待（默认）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `input_ack_wait` | 0.75 s | **input handler** 等待 write worker 的上限；超时回 `accepted-in-flight`（心跳不被拖过死期：0.75s < 2s） |
| `write_budget`（backend 内） | 1.5 s | 写路径的**发起取消截止**（budget 到期即取消并返回 partial）；**不是完成硬 SLA**：正常路径另计 join，取消持续异常/预算耗尽时可能超出（沿用 backend 验收口径） |
| `close_wait` | 8.0 s | **close handler** 等待 close worker 的上限（该 handler 持有生命周期锁 ≤ close_wait；describe/resize 不经该锁）；超时回 `closing` |
| `snapshot` barrier | 客户端 `timeout_ms`（≤60 s） | 由客户端请求决定；超时/失败显式降级（不假 full） |
| `lease_cleanup_retries` | 3 | 死期清理未收敛时的有界重试次数（0.5s 间隔） |
| `watchdog_join` / `finalize_budget` | 3 s / 12 s | 收尾有界：watchdog join、`_finalize` 总预算（超出 → 未证明，非零退出码） |
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
  "lifecycle": {"closing": false, "expiry_in_progress": false, "expiry_reason": null,
                "exit_reason": "ok", "accept_failure": null, "startup_cleanup_converged": true},
  "exit": {"seen": false, "code": null, "reason": "..."},
  "cleanup": {"state_after": "exited", "ok": true, "seconds": 0.3},
  "consumer": {"failed": false, "count": 0, "gap_seen": false},
  "snapshot": {"engine": "injected|none", "fidelity": "...", "recovery": "...", "feed_lag": false},
  "input_worker": {"in_flight": false, "completed": 3, "last_status": "done"},
  "close_worker": {"in_flight": false, "finished": true, "error_type": null},
  "connections": {"accepted": 2, "active": 1}
}
```

`detail` 的生成（O4 修正）：**字段级缩减**（嵌套长字符串逐级缩短 → 按可选块丢弃 →
极简骨架），保证输出始终是**合法** JSON 且 ≤ 4096；**禁止**字符串截断。

**F5 机器确认字段（仅 `snapshot` 响应 detail，已冻结实施）**：

```json
{"cursors_valid": true, "reset_unconfirmed": false}
```

- 来源：emulator 已知轻量确认面（`cursors_valid` 属性 + `diagnostics()`）；**不阻塞**
  watchdog/心跳，探测异常只记类型名、异常文本不进入响应（防泄漏）。
- 语义：来源**已核验**才 bool；缺失/异常 → `null`（**unknown，不默认有效**）。
- **一致性规则**：`cursors_valid=true` 仅在能与**本次响应载荷**一致可证明时透出
  （快照存在、`recovery ∈ {full, partial}`、无 `feed_lag`、`reset_unconfirmed` 非 true）；
  否则 `null`——**不机械读"当前 True"覆盖过时/降级/超界降级快照**。
- `null` / `false` / `reset_unconfirmed=true` 一律**不得**作为完整续流依据（`recovery != full`
  或本字段非 true 均应 fresh-view）。
- 两类字段在**任何** detail 缩减级别保留（`_PRESERVE_DETAIL_KEYS`），极简骨架与最终
  回退都携带；node 未验证序列原因仍只在 `note`（原因分层，见 §3.2 I-3）。

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
  - **异常语义（O4 措辞对齐）**：桥**不吞异常**——feed/feed_at 的异常穿透到
    runtime 的 `output_consumer` 隔离层（只记脱敏类型名 + 粘滞 `consumer_failed`），
    随后快照显式降级；桥自身不额外抛包装异常、也不静默继续假装 full。
- resize 与 feed 走**同一有序通道**：runner 调用 `emulator.resize(rows, cols)`；
  次序由仿真器内部通道保证（协议要求）；runner 不绕过、也不声称 PTY 与仿真器
  尺寸已同步（`detail` 如实分列）。

---

## 5. 生命周期命令与退出码（冻结）

| 码 | 常量 | 含义 |
| --- | --- | --- |
| 0 | `RUNNER_EXIT_OK` | 正常停止（显式 close / 死期自停且清理**已证明**收敛） |
| 2 | `RUNNER_EXIT_USAGE` | 命令行/用法错误 |
| 3 | `RUNNER_EXIT_CLEANUP_FAILED` | 死期清理有界重试耗尽仍未收敛（owner 保留证据在状态文件） |
| 4 | `RUNNER_EXIT_BOOTSTRAP_FAILED` | bootstrap/身份/管道/门禁失败，且自有资源清理**已证明**收敛 |
| 5 | `RUNNER_EXIT_INTERNAL` | 未预期内部错误（类型名脱敏） |
| 6 | `RUNNER_EXIT_CLEANUP_UNPROVEN` | 启动失败或收尾时清理**未能证明收敛**（含 `runtime LOST`、finalize 超预算）；owner/引用保留 |
| 7 | `RUNNER_EXIT_ACCEPT_FAILED` | accept 循环异常退出（即使后续清理收敛也**非** 0；清理状态见状态文件） |

| 事件 | runner 行为 |
| --- | --- |
| `stop`（close/service-shutdown/lease-expired） | close worker：身份核验→所有权快照→中断→终止→整树→reader 收敛→句柄；成功 → 退出 0 |
| 死期清理有界重试后仍未收敛 | `_lease_expired_close` 耗尽 → 退出 3 |
| 启动失败（含 token 入 env；hello 删除仅限未通过自检） | fail-closed，不监听；按清理证据给 4 或 6 |
| 正常运行中（含 detach 后） | 服务循环；不自行退出 |

- **状态文件（R3 + N2/N3：跨进程可观测的脱敏状态）**：
  `<terminals_root>/runner-status/<terminal_id>.json`（**串行写 + 唯一自有 tmp + 原子
  replace**；无秘密、无异常消息）。字段：`phase` / `exit_code` / `reason` /
  `runner_pid` / `runner_identity` / `prior_record_identity_mismatch` / `cleanup
  {converged, startup, pipe, retained, finalize, close_ok...}` / `updated_at`。
  - **路径安全（N2）**：写入前 `validate_terminal_id` 并确认收敛在自有 `runner-status`
    目录内；非法 id（分隔符/穿越/形状不符）→ **零派生文件**（bootstrap 失败路径同样
    不逃逸），只记脱敏诊断 `status-path-rejected`（不回显 id）。`status_path` 属性对
    非法 id 显式抛 `ValueError`。
  - **身份绑定（N3）**：`runner_identity = {pid, process_created_at_filetime（十进制
    **字符串**，跨 JS 不浮点）, verified, source, authority}`；来源为 bootstrap 时经
    内核读取的自身身份；**未能核验时明确 `source="unknown"`（pid/filetime null），
    不造身份**。旧残留记录身份不同 → 新记录标记 `prior_record_identity_mismatch=true`
    （消费者**不得**把旧记录当"当前"）。
  - **状态文件本身不是存活权威**：消费者必须与 bootstrap 记录 / DPAPI 秘密的
    pid + raw FILETIME 交叉核验（`authority` 字段内亦写明）。
  - 写入失败（含 replace 失败）只清**自有** tmp、不破坏旧文件、不吞 note
    （`status-write-failed` / `status-tmp-cleanup-failed`）。
  - 启动失败、正常退出、cleanup-failed、accept-failed 都会落盘；stderr 另有一行静态
    `exit code=N reason=<静态串>`。未收敛时 `cleanup.converged=false` 且 `retained`
    列出仍持有的资源名（backend/pipe）。
- **"已清理"与"未证明清理"必须区分**：退出码 4 vs 6 + 状态文件；**不得**把
  "进程退出关闭 guard 句柄"当作已证明清理——该内核兜底只在 r2 特定场景被端到端观测过，
  不作为通用保证，也不得在报告中泛化（见 §9）。
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
RUNNER_EXIT_USAGE = 2
RUNNER_EXIT_CLEANUP_FAILED = 3
RUNNER_EXIT_BOOTSTRAP_FAILED = 4
RUNNER_EXIT_INTERNAL = 5
RUNNER_EXIT_CLEANUP_UNPROVEN = 6
RUNNER_EXIT_ACCEPT_FAILED = 7

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
    def close(self, *, reason: str = "explicit-close",
              source: str = "explicit",                    # "explicit"|"lease"（内部仲裁；lease 需带世代）
              lease_epoch: int | None = None, lease_established: bool | None = None,
              wait: float | None = None) -> dict[str, Any]  # wait 覆盖本次有界等待
    def retry_startup_cleanup(self) -> dict[str, Any]      # 启动失败清理同 owner 重试（诊断/测试）
    def retry_pipe_cleanup(self, *, attempts: int = 2) -> dict[str, Any]
    @property
    def exit_reason(self) -> str
    @property
    def status_path(self) -> Path
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
- 启动失败清理状态与退出原因经 §5 状态文件跨进程可观察；`retry_*` 仅同进程入口
  （runner 即将退出时为诊断/测试提供，不改变退出语义）。

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

- `tests/test_terminal_runner.py`（Windows 真机 + 隔离临时数据根；**31 项**）覆盖：
  管理心跳保活 / 死期 2s 自停；观察者轮换不杀 runtime；detach 语义
  （能力覆盖注入下的同 PID/FILETIME + shell 变量保存 + 新 controller 重连）与
  受 ambient 限制时的显式拒绝；根死孙活整树清理；runner 硬死 → guard 内核清整树；
  startup/身份/认证拒绝；cleanup-failed 保 owner 重试；阻塞 write 不堵 watchdog；
  跨进程 secret 重连；token 无 argv/env/日志泄漏。
- **r2 门控新增（12 项）**：R1 close worker 迟成功/失败 + lease 丢失（消费同一 worker、
  不叠加、耗尽非零）；R2 三种仲裁（expiry 先拒绝 detach；detach 先跳过迟到 expiry；
  迟到心跳作废旧 expiry，含反向自停断言）；startup 清理双失败 owner 重试 + pipe 不收敛
  保引用；连接线程回收与共享总 deadline join；detail 合法有界 JSON（字段级缩减）；
  真机 startup-refuse（真实保 guard 清整树 + exit 4）、startup-uncleaned（exit 6 +
  状态文件 retained）、accept-fail（exit 7 + 状态可观测）。
- 证据：`audit/terminal/implementation/runner/`（d48 历史 + `r2/` 新证据：UTF-8 文本日志、
  机器可读 JSON、源文件 blob 哈希锚定、清理/残留扫描）；**不覆盖旧证据**。
  历史上未入库的 `pytest.log`/`stability_run_*.log`/4-fail 日志已在 r2 README 中
  明确更正为"不可核（.log 被 gitignore 拦截），不再声称存在"。

## 9. 已知边界 / 未验证（不得当作已解决）

1. **权威仿真器引擎未实现**：本层只提供桥接与降级语义；无引擎时快照
   `fidelity=unavailable`（不承诺恢复）。`emulator.py` 与 sidecar 属并行 TA。
2. **ambient Job 下的 durable detach 未实证**：本机宿主 Job 不可清除
   （breakaway 被拒），detach 语义在能力覆盖注入下验证，真实环境按 fail-closed 拒绝。
3. **内核退出兜底未泛化**：进程退出关闭 guard 句柄的清除，仅在 r2 的
   startup-uncleaned 场景做过端到端观测（记录在案），**不作为通用保证**；
   启动失败清理的"已清理/未证明"以状态文件与退出码 4/6 区分。
4. **Pan 服务/浏览器/Web/MCP/registry 接线未实现**（P2/P3）；浏览器不得直连 runner
   或持 token（§1），服务将来执行 attachment 授权。
5. **跨用户/跨主机拒绝未实测**（同 P1 IPC 边界）；`PIPE_REJECT_REMOTE_CLIENTS`
   仍只有结构性证据；同用户持 token 连接具备控制能力是信任模型内事实（非远程漏洞）。
6. **未做长稳压测**：lease 抖动、连接 churn、慢客户端背压只做有界重复
   （churn 方向性验证，不代表长稳）；`stop` 超时后的迟响应语义依赖 ipc 的
   `late_responses` 计数。
7. **旧 Windows build 的 ClosePseudoConsole/取消路径**未验证（沿用 backend 报告边界）。

---

## 10. 变更记录

- `2026-10-03` 首版（`d48f967e`）：runner/client 与 19 项真机测试、审计证据。
- `2026-10-03` **r2 返工（独立审查 `a12a6bd6` 后闭环；先失败后通过）**：
  - **R1**：`_closing` 不再永久豁免 managed lease——watchdog 在显式 close 未收敛/失败
    且 Pan 断开时，消费**同一** close worker 结果（迟到成功直通 `exited`）并有界重试
    （仅 worker 结束后另起，不叠加）；耗尽 → 退出码 3；输入门不恢复。
  - **R2**：detach 与 lease-expiry 在同一生命周期门仲裁（锁内复核 `detached` 与
    lease 世代）；expiry 先取得关闭权 → detach 明确拒绝（`lease-close-in-progress`）；
    detach 先完成 → 迟到 expiry 跳过；心跳世代（epoch）自增使迟到 expiry 作废；
    显式 close（含 detached）仍可终止。
  - **R3+O1**：启动失败清理保可重试 owner（backend/pipe 引用不丢、CloseReport 不丢弃）、
    有界重试；新增跨进程状态文件 `<root>/runner-status/<tid>.json` 与退出码 6（未证明）
    ——与 4（已清理）区分；`retry_startup_cleanup` / `retry_pipe_cleanup` 同 owner 重试；
    内核退出兜底**不泛化**（仅 r2 场景实测记录）。
  - **O2**：accept 异常即使清理收敛也非 0（退出码 7），公开脱敏原因（stderr + 状态文件）。
  - **O3**：连接线程对象回收、join 共享总 deadline（3s，非 N×2s）、watchdog 有界 join（3s）
    与 `_finalize` 总预算（12s）；不收敛如实标注。
  - **O4**：detail 改为字段级缩减（始终合法 JSON，不截断）；桥接异常穿透语义与 runtime
    隔离对齐（措辞修正）。
  - **O5**：预算分层改写（input 0.75 / close 8 / snapshot 客户端 timeout；write budget
    为发起取消截止、非硬 SLA）。
  - **O6**：证据更正（r2 README：未入库 .log 不可核，不再声称存在；新证据 UTF-8 .txt + JSON
    + 源文件 blob 锚定）。
  - **O7**：`LOST` 不再映射为 exit 0（未证明 → 退出码 6）。
  - 测试 19 → **31 项**（新增 12 项门控/真机；直连 + uv 各一次，core129+broadcast8
    隔离 pyte 另列）。
- `2026-10-03` **F5 机器确认字段透出（组合审查 `cabacc7f` B5 / 冻结文档 I3；先失败后通过）**：
  `snapshot` detail 新增 `cursors_valid` / `reset_unconfirmed`（可信来源 bool；缺失/异常 = null
  unknown；一致性规则保守——`True` 仅在可证明与本次载荷一致时透出，不机械读当前值覆盖
  过时/降级/超界快照；缩减 detail 全级别保留）；`reset_unconfirmed=true` 时 `cursors_valid`
  保守为 unknown；原因分层与 I1–I4 冻结约束写入 §3.2；测试 38 → **43**（新增 5 项 F5 门控：
  无引擎 / 旧协议 fake / 信任源与一致性 / 异常不泄漏 / 超界+长 note 缩减保留）。
- `2026-10-03` **r3 窄修（独立窄验 ROUND2 `f0bcd6fd` 后闭环；先失败后通过）**：
  - **N1**（中）：`_acquire_lease_close_right` 在 **lease 锁内原子**完成 expiry 复核 +
    取得不可撤销关闭权，与 `owner_heartbeat` 线性化——hb 先 → 旧 expiry `renewed` 作废；
    expiry 先 → hb 锁内必返 `closing`（**不假 ok**、不续约）。R1 同 worker 消费/重试、
    输入门保持不回归。
  - **N2**（低）：`_status_path` 先 `validate_terminal_id` + 目录收敛校验；非法 id
    **零派生文件**（bootstrap 失败路径不逃逸），只记 `status-path-rejected`。
  - **N3**（低）：状态绑定 `runner_identity`（真实 pid + raw FILETIME 字符串；未核验
    明确 unknown 不造身份）+ `authority` 说明（非存活权威，须与 bootstrap/secret 交叉
    核验）+ `prior_record_identity_mismatch`（旧残留身份不同不当当前）；状态写串行、
    唯一自有 tmp、replace 失败只清自有不吞 cleanup。
  - **N4**（口径）：`_finalize` 总预算（入口起 12s）**包含** lifecycle 锁的有界等待与
    close worker 等待（此前连接/watchdog join 各自有 3s 分段预算）；锁忙 → 非零
    （cleanup-failed）、不假 success、不丢 owner、
    不裸关在途句柄，`finalize` 结果入状态文件；预算耗尽不静默延长（未证明码 6）；
    在途 close 复用原 worker；声明为调用方侧有界等待，非 OS 原语硬 SLA。
  - 测试 31 → **38 项**（新增 7：N1×2、N2×1、N3×2、N4×2；直连 + uv 各一次，
    不再运行 core129/8）。
