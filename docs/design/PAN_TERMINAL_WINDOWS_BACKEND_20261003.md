# Pan Terminal：Windows 生产 PTY 后端（ConPtyBackend）实现报告（2026-10-03）

- 任务：T-TERMINAL-PTY-20261003 / P1「Windows 生产 backend」（TA-B 子集：backend / spawn / guard / identity）。
- 工作树：`D:/project/pan-worktrees/terminal-backend-implement-20261003`
  （branch `implement/terminal-backend-20261003`，起点 `ed956bb9`，P0 两提交 + spawn spike 链已集成）。
- 写范围（全部新增，**未修改任何既有文件**）：`packages/core/terminal/{backend,spawn_win,guard,identity}.py`、
  `tests/test_terminal_{windows_backend,spawn_gate,identity,guard}.py`、本文件、
  `audit/terminal/implementation/backend/evidence/`（仅本任务新证据）。
- 不在本交付：`runner/service/ipc/win_pipe/secret_store/emulator`、依赖锁（未安装任何全局包、未改
  `minimal-requirements.txt`）、`contracts/__init__/runtime/ownership/registry`（A 独占，未触碰）。
- 定位：**真实内核原语实现 + 真机验证报告**；仍是首版实现，未做跨 build/长稳压测，边界见 §7。

---

## 0. 结论速览（实测 / 注入 / 未验证 分级）

| 项 | 结论 | 分级 |
|---|---|---|
| 挂起创建→assign→成员证明→exactly-one resume | 成立；失败分支全部 fail-closed 且清理可重试 | 实测 |
| 四证据门禁 `UnverifiedOwnershipGate` | 由真实副产物装配（成员查询 / 保留句柄 FILETIME / resume 返回 1），篡改任一要素即拒绝 | 实测 |
| `build_runtime` 真实运行（门禁→running→尾部输出→关闭） | 成立（`backend-runtime-assembly` 证据） | 实测 |
| read：真实 EOF 只认对端断链 | 成立；**子进程自然退出不产生 EOF**（HPCON 未关时），`ClosePseudoConsole` 后即 109 | 实测 |
| 进程退出 ≠ EOF / alive≠false 不当 EOF | 成立（空读 `b""`，交 runtime `eof_grace`） | 实测 |
| write：partial + 有界预算 + close 并发取消 | 成立（30s 预算被 close 中止：995 → `BackendClosedError`，close 12ms 完成） | 实测 |
| resize 到达真实子控制台 | 成立（100x30 → 132x43，子进程自报） | 实测 |
| exit_code：仅 signaled 提供（含真实 259） | 成立 | 实测 |
| kill_verified：单句柄、错身份/unknown 不杀 | 成立（差 1 FILETIME 拒绝；精确匹配才终止） | 实测 |
| Job 整树：根死后枚举孙进程；TerminateJobObject 后 active=0 | 成立（不靠 psutil、不按 PID 扫） | 实测 |
| holder 硬杀 → 内核 kill-on-close 收树 | 成立（holder 无清理代码机会） | 实测 |
| Ctrl-C（OS 级 CTRL_C_EVENT） | **未证实**：`\x03` 与 win32-input-mode 序列都不触发；仅“阻塞中的控制台读被释放” | 实测（负结论） |
| 旧 build（<26100）ClosePseudoConsole 阻塞行为 | 未验证；实现按官方建议先关输出 + 有界 worker（超时保留 HPCON 可重试） | 未验证 |
| detach 宿主可行性 / 嵌套 ambient Job 寿命 | 不在本模块；继续按计划 §5.1/§7 记录为产品门 | 未验证 |

---

## 1. 交付与接口

| 文件 | 内容 |
| --- | --- |
| `packages/core/terminal/identity.py` | 延迟绑定 kernel32（导入零副作用）；`wait_state`（`WaitForSingleObject` 三态）；`probe_process/probe_handle`（`ProcessProbe` 三态，查不到=UNKNOWN）；raw64 FILETIME（跨 JS 一律字符串）；`kill_verified`（同句柄 SYNCHRONIZE\|QUERY\|TERMINATE，FILETIME 精确匹配 + signaled 等待）；`cancel_synchronous_io` / `open_current_thread_handle` / `close_handle_checked` / `get_process_handle_count` |
| `packages/core/terminal/guard.py` | `JobObjectGuard`：`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + **禁 breakaway**（回读 LimitFlags 自证）；`member_pids`（`JobObjectBasicProcessIdList` 有界扩容）；`terminate_tree`（先枚举 → `TerminateJobObject` → 轮询 active==0 双证据）；`remaining` 按 Job 成员核对；句柄关闭后查询抛 `GuardQueryError`（fail-closed） |
| `packages/core/terminal/spawn_win.py` | `spawn_conpty_suspended`：CreatePipe×2 → CreatePseudoConsole → 属性表 → `CreateProcessW(CREATE_SUSPENDED\|EXTENDED_STARTUPINFO_PRESENT)` → 释放 pty 侧两管道句柄 → 保留句柄读 FILETIME → assign → `IsProcessInJob` 证明 + active≥1 → `ResumeThread`（先前挂起计数必须恰为 1）；`SpawnEvidence`/`SpawnDenied`；`ConPtySpawn` 分阶段释放（失败保留、重试幂等）；`build_command_line/build_environment_block` |
| `packages/core/terminal/backend.py` | `ConPtyBackend`（`PtyBackend` 协议）：pump 线程 + 有界缓冲；`read`（阻塞/有界；真实 EOF≠进程退出）；`write`（串行、partial、有界预算、可取消）；`resize`；`alive`（三态，unknown 抛 `BackendProbeError`）；`exit_code`（仅 signaled，含 259）；`terminate`（只经保留句柄 + 有界 signaled 确认）；`close`（stop/CancelSynchronousIo/join → writer 收敛 → 官方顺序释放 → 自持 guard 最后关闭；失败抛 `BackendCloseError` 保留 owner，重复幂等）；`gate`（`UnverifiedOwnershipGate`）；`WinptyBackend` 占位（非生产依赖，构造即拒绝） |
| `tests/test_terminal_windows_backend.py` | 29 项（装配、259、resize、Ctrl-C 边界、≥1MiB 有界日志、**pump 缓冲边界 cap+单块**、自然退出≠EOF、真实 EOF、写堵塞并发、取消 join、阻塞读不阻塞 write/terminate/close、写预算 watchdog、interrupt 有界（含 R10 直接 write 路径）、DEAD 绑定 retained handle、close 活进程前置拒绝且 IO 存活（R3）、writer 收敛需输入锁（R4）、写 timer 空隙取消重试（R7）、**取消 worker 先 join 再关句柄（R8）**、预算含锁等待（R9）、**取消者配对所有权/close 预收敛（R12）**、**取消者异常重试与 cap 显式诊断（R13）**、pump 句柄失败可见可收敛（R6/R5）、**装配必须接 backend.probe（R11）**、清理失败重试×2、幂等/探针 fail-closed、句柄回归、平台边界） |
| `audit/terminal/implementation/backend/repro/` | 返工复现与探针脚本：`repro_converge_failopen.py`（R4 先失败/后通过）、`repro_write_timer_gap.py`（Timer 空隙先失败/后通过）、`probe_ctrl_c_candidates.py`（Ctrl-C 四候选 A/B/C/C3） |
| `audit/terminal/implementation/backend/evidence/r2/` | 本轮（r2）新证据：先失败/后通过 JSON、探针 JSON、48 项测试证据（旧证据目录未动） |
| `tests/test_terminal_spawn_gate.py` | 10 项（纯逻辑 env/命令行 + 挂起无副作用/exactly-one resume、assign/成员/查询/异常 resume 门禁、清理重试、门禁装配、失败不发布） |
| `tests/test_terminal_guard.py` | 6 项（flags/describe、成员与 remaining、根死枚举孙进程、idle terminate/idempotent、close 后 fail-closed、holder 硬杀） |
| `tests/test_terminal_identity.py` | 9 项（平台守卫、ALIVE/DEAD 证据、UNKNOWN 不等于 dead、259 歧义、kill_verified 拒绝/成功、**原异常不被掩盖（审查 R2 核验）**、FILETIME 字符串、CloseHandle 检查、取消原语） |

协议兼容：`ConPtyBackend` 满足 `contracts.PtyBackend`（`isinstance` 校验）、`JobObjectGuard` 满足 `TreeGuard`；
`read` 的 `timeout=` 为**可选扩展**（默认仍是契约的阻塞语义），供有界轮询与测试使用。

---

## 2. 关键设计决策（与 P0 契约 / 计划对齐）

1. **原子 spawn 的失败语义（计划 §5.3 / 接口文档 §4.2）**：任何门禁不可证 → 不 resume、不返回 session、
   不发布 running；只终止**本次自建**子进程（保留句柄），随后按官方顺序分阶段释放；失败资源保留在
   `SpawnDenied.cleanup["retained"]` 并可用 `retry_cleanup()` 重试（spike s13 语义移植，但资源归属与
   官方顺序在本实现内重新实现，不依赖 spike 代码）。
2. **注入钩子如实标注**：`assign_impl / membership_impl / resume_impl` 仍是**真实内核调用**——用无效
   job/process handle 触发真实 `ERROR_INVALID_HANDLE`，用双重 `ResumeThread`（真实返回 0）与
   `SuspendThread+ResumeThread`（真实返回 2）构造异常 resume。它们是测试构造，**不是**真实 OS 故障复现。
3. **EOF 语义以实测为准**：本机 build 26200 实测“子进程自然退出后输出管道不 EOF；`ClosePseudoConsole`
   之后 `ReadFile` 报 109”。因此：`read` 只在 109/0 字节读时抛 `EOFError`；进程已退出且静默窗口
   （默认 150ms）后返回 `b""`，把“输出是否完整”交回 runtime 的 `eof_grace` 判定（不冒充 EOF）。
4. **terminate 的有界确认**：实测 `TerminateProcess` 到对象 signaled 存在 ~0.5s 级延迟（ConPTY 客户端
   在控制台阻塞时尤甚）。若“发起即返回”，runtime 的“根确认退出”会立即 fail-closed。因此 terminate 在
   **有界预算（默认 1.0s）**内等待 signaled 后返回；预算耗尽不抛错但记录
   `last_terminate_confirmed=False`（terminate 仍是“请求”语义，最终确认归调用方存活探针）。
5. **写入与 close 的竞态防线**：writer 在 `_input_lock` 内注册 OS 线程句柄（`OpenThread(THREAD_TERMINATE)`）
   后执行阻塞 `WriteFile`；close 通过注册表 `CancelSynchronousIo` 取消（实测：进行中的 WriteFile 被
   中止为 995，writer 抛 `BackendClosedError(written=...)`）；close 在取得 `_input_lock` 后才释放
   `input_write`，杜绝句柄复用/释放竞态；预算耗尽返回 partial（不抛）。
6. **close 的诚实性**：分阶段（reader 收敛 → writer 收敛 → 官方顺序释放 → 自持 guard 最后关闭）；
   任一阶段失败**抛** `BackendCloseError`（runtime 的 `_bounded_backend_close` 以异常判定失败），报告
   携带 `retained`；重复 close 幂等；`ClosePseudoConsole` 用有界 worker（旧 build 阻塞边界如实声明，
   超时保留 HPCON 可重试）。
7. **身份与终止**：`kill_verified` 全程单句柄（打开一次），unknown/不匹配/已退出一律拒绝；探针
   “查不到/打不开=UNKNOWN”，绝不冒充 dead；FILETIME 精确相等（无容差）。

---

## 2.5 r3 对齐（核心窄修 `b5017d1d`，集成树 `pr6-terminal-20261002`；本工作树按指示**不 cherry-pick/不改 core**）

读取集成树最新 `contracts.py`/`runtime.py` 与 CORE_INTERFACES **§13** 后，本后端在自身范围内逐条满足
四类新约束（不改方法签名）：

| r3 约束 | 本后端的落点 | 锚点用例/证据 |
|---|---|---|
| `PtyBackend` 句柄级并发：read/write/resize/terminate/close 必须由后端内部串行化；close 与在途 write/terminate 不得竞态 | read 走 `_cv`（等待期间不持锁）；write 走**有界** `_input_lock` + writer 注册表；resize 与句柄释放走 `_op_lock`；close = 关门（`_closing`）→ CancelSynchronousIo+join 读线程 → writer 收敛（注册表空 + 输入锁可得）→ 才释放句柄。“读阻塞不持全局锁”：write/terminate/alive/探针都不需要 `_cv` | `backend-read-nonblocking`（阻塞 read 时 write<1s、alive/探针<0.5s、close 有界收敛） |
| 输入门后的**已接纳输入有界收敛**（runtime `input_drain_timeout`=2.0s） | write 总预算默认 **1.5s**（<2.0s）；**预算计时器**到期即对写线程 `CancelSynchronousIo`，单次被堵塞 WriteFile 也按期返回 partial；写锁同样有界获取（不可得 → `BackendBusyError`） | `backend-write-budget`（32MiB 堵塞写 0.6s 预算内返回；`timeout=0` 立即 partial(0)） |
| runtime.close **interrupt 阶段**直接 `backend.write(b"\x03")`/alive/探针，必须自身有界 | interrupt 用**短预算 0.5s**；alive/`backend.probe` 是 `WaitForSingleObject(0)`+`GetProcessTimes` 快速查询；`terminate(True)` 的有界确认 ≤1.0s（runtime 给 1.5s worker 预算） | `backend-interrupt-bounded`（在途写持锁时 interrupt <1.5s 返回且 close 收敛） |
| `ProcessProbe(DEAD)` 只能绑定 spawn 时**同一 retained handle**或 Job 证据；不得用现查陌生 PID 的 signaled 放行 | 新增 `ConPtyBackend.probe(pid)`：非本终端 PID 一律 UNKNOWN；本终端根 PID 走 retained handle（释放后走保留的 retained/Job 死亡缓存）；`identity.probe_process` 改为只给 ALIVE/UNKNOWN（fresh-handle signaled 不再作为 DEAD）；runtime 装配统一改用 `backend.probe` | `backend-probe-binding`、identity `test_probe_unknown_is_not_dead`（retained=DEAD vs fresh=UNKNOWN 对拍） |
| `TreeGuard` 每个操作自身有界、快速返回 | `owned_pids`/`remaining` 单次 `QueryInformationJobObject`（成员列表扩容循环有界）；`terminate_tree` = 单次 `TerminateJobObject` + 受 `timeout` 约束的轮询，上限钳制 `MAX_TERMINATE_TREE_TIMEOUT=30s`；无阻塞等待 | `test_terminate_tree_idle_and_repeatable`、`test_guard_query_fail_closed_after_close` |

---

---

## 2.6 审查返工 r2（独立审查 `8112788c`；先复现/先核验，后修复）

本轮从 `430ba7f5` 出发，按审查 §4（R1–R6）与 MA 补充分析逐项处理；故障类先做**确定性先失败复现**，
证据在 `audit/terminal/implementation/backend/evidence/r2/`（旧证据目录未动）。
先失败证据的出处：同一复现脚本在 `git archive 430ba7f5`（修复前代码）副本上重放生成（`*-pre-*.json`），
后通过证据由当前代码同脚本生成（`*-post-*.json`）；两组 JSON 均含 `phase` 与判定字段。

| 审查项 | 处理 | 证据（先失败 -> 后通过） | 用例/锚点 |
| --- | --- | --- | --- |
| R1 Ctrl-C（高，产品缺口） | **有界定位完成，机制在本机不可行：R1 保持未验收，交 MA 决策**（见 §2.6.1） | `probe-ctrl-c-candidates-*.json`（A/B/C/C3 四候选全负） | `test_ctrl_c_input_channel_and_survival`（不变：仍只声明“读被释放 + 通道可用”） |
| R2 `kill_verified` “None AttributeError 掩盖” | **核验后不成立**：`_kill_verified_with_handle` 抛错时 finally 关句柄后**原异常直接从 try 传播**，`result.handle_closed = ...` 根本不会执行——不存在 NoneType 属性错误掩盖原异常。代码不做机械“修复”，仅补控制流注释防误修 | 新增注入测试 | `test_kill_verified_original_exception_not_masked` |
| R3 直接 `close()` 对活进程先破坏 IO | **修复**：close 增加死亡/所有权**前置门禁**（step 0）——活/unknown 一律 `BackendCloseError` 拒绝，且不停 pump、不关输入门、不取消读、不释放句柄；拒绝后 read/write/resize 继续可用。runtime“先 terminate 后 close”语义不变 | 修复前 probe3 Part B（审查树）；修复后本树新用例 | `test_close_refused_on_live_backend_io_survives` |
| R4 `_converge_writers` fail-open | **先复现**（持锁未注册的线程 -> `converged=True`）**后修复**：收敛 = 注册表为空 **且** 确实取得 `_input_lock`；持锁未注册的写者必须收敛到“拿得到锁”为止，否则失败保 owner（句柄不提前释放） | `repro-converge-failopen-{pre,post}-*.json` | `test_writer_convergence_requires_input_lock`（门控负例先失败后通过） |
| R4'（MA 补充）write 预算 Timer 窗口 | **先复现**（取消落空在“检查后/WriteFile 前” -> 超出预算 5s 仍阻塞）**后修复**：取消者**重试**直到本次写调用结束；`timer.join(1.0)` 先于线程句柄关闭（未收敛 -> 句柄转 orphan，不当作已回收）；**预算含锁等待**（timer 只拿剩余时间） | `repro-write-timer-gap-{pre,post}-*.json` | `test_write_timer_gap_canceller_retries`、`test_write_budget_includes_lock_wait` |
| R5 pump 诊断 | **修复**：`_pump_exit`（eof/cancelled/error:\<Type\>/fatal:\<Type\>/stopped-requested）+ `_pump_fatal` 显式公布；`_converge_reader` 所有返回路径与 `describe()`/close 报告均含这些字段 | 48 项测试证据 | `test_read_cancel_join_on_close`（新增断言）、`test_pump_handle_failure_visible_and_drained` |
| R6 pump 句柄获取失败静默死线程 | **修复**：`_pump_loop` 全程 try/except/finally——任何线程级异常都被捕获、脱敏记录（类型名）并置 `_drain_done`；`read` 在“pump 已终止且无 eof/error”时抛 `BackendPumpError`（不静默、不冒充 EOF） | 注入 `open_current_thread_handle` 失败 | `test_pump_handle_failure_visible_and_drained` |

### 2.6.1 Ctrl-C：有界定位结论（R1，保持未验收）

官方原文（learn.microsoft.com）：
- `GenerateConsoleCtrlEvent`：**CTRL_C_EVENT 无法定向到非零进程组**（"If dwProcessGroupId is nonzero,
  this function will succeed, but the CTRL+C signal will not be received by processes within the
  specified process group"）；`dwProcessGroupId=0` 向**调用者共享控制台**的所有进程投递；
- `SetConsoleCtrlHandler(NULL, TRUE/FALSE)`：忽略/恢复 Ctrl+C（可继承），仅作自建诊断；
- `WriteConsoleInput`：向控制台输入缓冲区写记录（需 GENERIC_WRITE 输入句柄；attach 后用 `CONIN$` 重开）。

实测（build 26200；全部自建进程 + 真实 `SetConsoleCtrlHandler` 子进程；“不读 stdin 计算”与“读取中”
两种形态；四个候选，证据 `probe-ctrl-c-candidates-*.json`）：

| 候选 | 结果 |
| --- | --- |
| A `write(b"\x03")`（现状） | 无事件；阻塞中的控制台读被释放；子进程存活（负） |
| B helper `FreeConsole -> AttachConsole(child) -> GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)` | attach 成功、目标在 `GetConsoleProcessList` 内、调用返回成功——**handler 仍不触发**（负；与审查 probe1b 一致） |
| C helper attach + `WriteConsoleInput`（Ctrl+C 键 down/up，非读取子进程） | 初次 `GetStdHandle` 句柄无效（err=6）→ 改用 `CONIN$` 后**写入成功（2/2 记录）但无事件**（负） |
| C3 同上，子进程正在 `readline` | 写入成功、读未被释放、无事件（负） |

结论：**在本机/本 build 的安全接口集内，无法把 Ctrl-C 实现为真实 OS 级 CTRL_C_EVENT 中断**。
正控制（审查 probe1c：`ClosePseudoConsole` 触发 CTRL_CLOSE_EVENT）证明 handler 通路本身可用，
缺口在“输入 → 控制事件”的翻译路径。按指示：不自行降级产品、不做无界实验、不改计划承诺——
**R1 保持未验收**，交 MA 在「挂起验收 / 明示 best-effort 并同步计划与用户文档 / 换环境再验」间决策；
本后端维持 `terminate(force=False)` 的 best-effort 语义（短预算 `\x03`，注明不承诺 OS 级语义）。

### 2.6.2 审查 round-2（`21fdb236`/`536b4a1f`，对 `430ba7f5` 的窄复核）增量

审查 round-2 已提交证据（只读引用，未改他树）：`probe7`（写预算竞态 R7/R8/R9/R10）、`probe8`
（DEAD 绑定 + R11 组合）、`probe9`（R2 撤回）与 `supplement-r2`（uv+pyte 137/137）。本轮逐项处理：

| # | round-2 事实（审查实测） | 处置 | 本树先失败证据 |
| --- | --- | --- | --- |
| R7 | 单次取消在“检查后/WriteFile 前”落空：3.5s（>2×预算）仍未返回，**外部 cancel 才解除（未证明永久堵塞，不夸大）** | 已在 `3b83f939` 修复（取消者**重试**至本次调用结束） | `repro-write-timer-gap-pre-*.json`：>5s 仍阻塞、且**close 外部取消后解除**（`writer_done_after_close=true`）——与“未证明永久堵塞”一致 |
| R8 | `timer.cancel()` 不 join：回调在 thread_handle 关闭后仍 `CancelSynchronousIo` → **err=6**（陈旧句柄；复用窗口真实） | 已在 `3b83f939` 修复（`timer.join(1.0)` 先于关句柄；未收敛转 orphan 不当作已回收） | 430 归档**测试级重放**：`err=6`（`repro-r8-r9-r10-pre-tests.json`）；修复后 `test_write_cancel_worker_joined_before_handle_close`（断言 err≠6 且 close ≥ cancel 返回） |
| R9 | 锁等待 1.4s + 全额 timer → 总 **2.867s ≈2×budget**（> input_drain_timeout 2.0s） | 已在 `3b83f939` 修复（timer 只拿**剩余**时间）。**R14 修正口径**：正常路径总时长 ≈ budget（含锁等待），但 budget 是**发起取消的截止**而非硬 SLA——单次 write 结束的 join（≤1.0s）与故障 cap（max(5.0, 4×budget)）单列，取消者持续异常/cap 耗尽时可能超出（见 §2.8） | 430 重放：**2.923s**；修复后 `test_write_budget_includes_lock_wait`（1.4s 锁 + 1.5s 预算 → <1.9s） |
| R10 | runtime interrupt 直接 `write(b"\x03")` 用**默认预算 1.5s**，与 `terminate(False)` 的 0.5s 不同；**有界≠有效** | 本轮修复：`write` 对孤字节 `b"\x03"`（timeout 未给出）自动使用 interrupt 预算 0.5s，与 `terminate(False)` 统一 | 430 重放：`DID NOT RAISE BackendBusyError`（>1.0s；审查 probe7-E 1.501s vs 0.511s）；修复后同用例断言 runtime 路径 <1.0s。**Ctrl-C 负结论不变**（§2.6.1）：此处仅统一上界，不宣称中断有效 |
| R11 | 宿主接 `identity.probe_process` → 根已死时清理**永久 refusal**；必须接 `backend.probe`（probe8 组合验证 26/26） | 本轮：§5 装配说明改为 `backend.probe`（含原因）；新增 `test_runtime_assembly_requires_retained_probe`（fresh→refused/owner retained；retained→EXITED；直接 `backend.close` 可恢复） | 测试内两路对拍（fresh 先失败、retained 后通过），证据 `backend-probe-assembly` |
| R4 | probe6 round-2 仍复现 `converged=True`（430 未含修复） | 已在 `3b83f939` 修复（判定 = 注册表空 **且** 取得输入锁） | `repro-converge-failopen-{pre,post}-*.json`（pre 重放于 430） |
| R2 | **审查 round-2 已撤回**（probe9：精确注入实测原异常原样传播、finally 正常关句柄、目标未被触碰） | 与本报告 §2.6 的核验结论一致；保留注入测试与防误修注释 | `test_kill_verified_original_exception_not_masked` |

口径补充（round-2 probe4）：pump 缓冲**不是严格 ≤cap**——pump 追加一个读块后才检查并暂停，
真实边界为 `len(buf) ≤ buffer_cap + 单块（≤ read_size）`（审查配置：32768 + 65536 → 最大 98304；
实测峰值 32810）。本树默认 cap=4MiB、read_size=64KiB（上限 4MiB+64KiB），并新增
`test_pump_buffer_bound_cap_plus_block` 锚定该公式（不丢数据）。

---

## 2.8 审查 round-3（audit/2，`d9d901d2` 对 `3b83f939` 的独立验收）——R12/R13/R14 窄修

audit/2 结论：48 项 + 137 核心（主环境 130+7 skip；uv+pyte 137/137）全绿；
**R3/R4/R4b/R4c/R5/R6 接受**（含 R4b 取消重试 3 落空+1 命中 0.369s、R4c 锁 1.2s+预算 1.5s→1.522s、
pump fatal 可见可收敛）；新提 **R12/R13**（所有权竞态）与 **R14**（口径）。本轮窄修：

| # | audit/2 实测事实 | 处置（本窄修） | 先失败 → 后通过 |
| --- | --- | --- | --- |
| R12 | 回调阻塞 > join(1.0s)：write 返回后 `_orphan_handles=[792]`（**仅 handle、无 timer**）；close 在取消者 in-flight 时 `CloseHandle`，句柄值 792 **立即复用**，晚回调打陈旧/复用句柄 | **保 (timer, handle) 配对**（`_CancelWorkerRecord`）；与普通 `CloseHandle` 失败孤柄**分类型**（`_orphan_handles` vs `_cancel_workers`）；write 结束时 join(≤1.0s) 超时→配对保留且在 `describe()`/close 报告可见；close **新增 2b 预收敛**：释放**任何**句柄前对所有配对做有界 join + `is_alive` 真核验，未收敛 → `cancel_worker_not_converged` 拒绝（owner retained、可重试）；join 在锁外、锁内仅快照/回收（不在锁内无界等待） | 661 重放：`cancel_workers=[]` → 新增 `test_close_refuses_while_cancel_worker_in_flight`（拒绝 + 句柄未裸关 + 回调释放后 err≠6 + 同 owner 重试成功） |
| R13 | `cancel_synchronous_io` 异常未捕获 → Timer **静默死亡**（attempts=1），write 2.0s≈6×budget 仍阻塞 | 取消循环 try/except：异常**脱敏分类**（仅类型名）并**继续有界重试**；命中/落空(false)/异常/cap 分别诊断；`_last_cancel_diag` **实时发布**（write 仍阻塞时也可见）；cap 耗尽显式置位，**Timer 退出 ≠ writer 退出**（writer 收敛真证据仍是注册表 + 输入锁） | 661 重放：一次异常后 write 6s 未返回；cap 无诊断 → `test_cancel_worker_exception_retries_desensitized`、`test_cancel_worker_cap_exhausted_diagnosed_and_recovers` |
| R14 | cap `max(5,4×budget)` 实测 242@7.5s、writer 仍阻塞：**budget 非硬保证** | 口径与实现对齐（非仅删承诺）：`budget` = **发起取消的截止**；正常路径含锁等待；单次 write 结束的 join（≤ :data:`_CANCEL_JOIN_SECONDS`=1.0s）与 fault cap（`max(_CANCEL_CAP_BASE_SECONDS=5.0, 4×budget)`）**单列**；取消者持续异常/cap 耗尽且目标 I/O 仍阻塞时 write **可能超出预算**（未解决限制）；close 的 `_converge_writers` 与 runtime 在途输入有界拒绝为兜底，**不宣传后端 write 硬有界**；当前窄修不引入新架构 | 两用例的"writer 仍阻塞"断言（`state["done"].is_set() is False`）+ cap/异常诊断断言 |

哨兵泄漏防护：诊断只记类型名/计数；两个 R13 用例注入带哨兵消息的异常并断言
`json.dumps(describe(), ensure_ascii=False)` 不含哨兵；所有诊断字段有界（固定键 + 计数）。

证据：`evidence/r3/`（`repro-r12-r13-pre-tests.json`：661 归档测试级重放 3 failed；54 项测试证据，
含 `backend-cancel-owner-pair` / `backend-cancel-exception` / `backend-cancel-cap`）；
`evidence/r2` 与 review 侧证据**未动**、未重生成。

---

## 3. 本机实测事实（build 26200，供后续校准）

| # | 事实 | 证据 |
|---|---|---|
| 1 | 子进程自然退出（HPCON 未关）：输出管道**不 EOF**（6s 窗口无事件） | `backend-runtime-assembly`（`eof-timeout`）与 `backend-natural-exit` |
| 2 | `ClosePseudoConsole` 后：`ReadFile` 立即 109（ERROR_BROKEN_PIPE） | `backend-real-eof` |
| 3 | `TerminateProcess`→signaled ~0.5s 级延迟（ConPTY 客户端在控制台阻塞时）；不等待会让 runtime 的“根确认退出”立即 fail-closed | `backend-runtime-assembly`、`test_gate_wiring_and_runtime_start`（修复后正式用例全绿；此前在 runtime 装配流 0/6 → 6/6 复现验证） |
| 4 | `CancelSynchronousIo` 对进行中的 ReadFile/WriteFile 有效（995；无挂起时 1168） | `backend-read-cancel`、`backend-write-close`、identity `test_cancel_synchronous_io_no_pending_returns_not_found` |
| 5 | Job：`JobObjectBasicProcessIdList` 只列活跃成员；根死后孙进程仍可枚举；`TerminateJobObject` 后 active=0 且列表清空 | `test_root_death_still_enumerates_whole_job`、`test_terminate_tree_idle_and_repeatable` |
| 6 | holder 硬杀（无清理代码）→ kill-on-close 收掉整树 | `test_holder_hard_kill_kills_tree` |
| 7 | `\x03` / win32-input-mode 序列**不产生** OS 级 CTRL_C_EVENT；仅“阻塞中的控制台读被释放” | `backend-ctrl-c`（负结论，如实保留） |
| 8 | 退出码 259 与 signaled 语义分离正确（信息字段 259 + wait_state dead） | `backend-exit-259`、identity `test_exit_code_259_ambiguity_with_retained_handle` |
| 9 | 句柄计数回归：1 次预热后 3 次 spawn/terminate/close 循环增长 ≤2 | `backend-handle-count` |

---

## 4. 测试与证据

固定命令（隔离环境=miniforge 自带 pytest + pytest-timeout，纯 stdlib，无第三方依赖）：

```bash
cd D:/project/pan-worktrees/terminal-backend-implement-20261003
PAN_TERMINAL_EVIDENCE_DIR=audit/terminal/implementation/backend/evidence \
E:/software/miniforge/python.exe -m pytest tests/test_terminal_identity.py tests/test_terminal_guard.py \
  tests/test_terminal_spawn_gate.py tests/test_terminal_windows_backend.py -q -p no:cacheprovider
```

结果（2026-10-03 本机，Windows 11 build 26200；审查返工 r2+round-2 口径）：
**54 passed in 26.21s**（identity 9 / guard 6 / spawn_gate 10 / windows_backend 29），`pytest.ini` 的
`timeout=300` 作为看门狗兜底；所有等待均有显式上界。旧证据 14 份保持原样；本轮（r2）新证据在
`audit/terminal/implementation/backend/evidence/r3/`（30 份：29 份测试证据 + R12/R13 的 661 归档重放 1 份；**r2 的 32 份与 review 证据均未动、未重生成**）；旧口径 `evidence/r2/`（32 份：26 份测试证据 + 6 份手工证据，
含 R4/R7/R8/R9/R10 的 pre/post 对、Ctrl-C 四候选探针、430 归档测试级重放）。原 spawn spike 证据
（`audit/terminal/codex/spawn/evidence/`）**未触碰、未覆盖**。

全量 `tests/`（回归参照）：**7 failed, 1945 passed, 15 skipped**（`430ba7f5` 时点串行口径）；
失败集与逐项归因见 §6。按 MA 指示本轮**不重复全库**（审查 TA 已独立复跑 38+137 与 6 项既有失败）；
并发运行两份 pytest 时会出现性能类波动（如 `test_session_incremental.py`），口径一律以串行为准。

---

## 5. 生产门禁与安装说明（供 runner/service 接线的硬约束）

1. `ConPtyBackend.spawn(...)` 的 `backend.gate` 必须作为 `runtime.start(gate=...)` 传入；缺 gate 时
   runtime 自身 fail-closed（`OwnershipGateError`）。
2. `build_runtime(..., identity=backend.identity, identity_probe=backend.probe)` ——**必须**接
   `ConPtyBackend.probe`（r3 §13.4 绑定版：DEAD 只来自 spawn 时同一 retained handle / 保留的
   retained-Job 证据）。**不得**接 `identity.probe_process`：它对现查 PID 的 signaled 结果只给
   UNKNOWN（r3 §13.4 的反向保护），会让“根已死”的终端的清理被 identity-unknown **永久拒绝**
   （owner retained、无法自行收敛；审查 probe8 + 本树
   `test_runtime_assembly_requires_retained_probe` 均实证）。正确/错误接线的行为差异见 §2.6.2。
3. `runtime.close()` 的 `terminate` 步骤会调用 `backend.terminate(True)`（有界确认）；`backend.close`
   只能在其后（根确认退出后）被调用——直接对存活进程调用会得到 `BackendCloseError`
   （`process_not_confirmed_dead`），这是**有意**的 fail-closed，不是缺陷。
4. 自持 guard：`spawn` 默认自建 Job（`owns_guard=True`，`close()` 最后关闭它 = kill-on-close 兜底）；
   若外部注入 guard（如 runner 复用），`owns_guard=False`，guard 关闭归注入者。
5. 非 Windows：`spawn/构造` 立即 `BackendUnavailableError`（含可执行提示语义），不得静默降级。

---

## 6. 通过 / 失败 / 未测（如实清单）

**通过（本任务交付，38/38）**：见 §1 测试文件与 §4 命令。

**失败（全量 `tests/` 回归，7 项；逐项归因并含 HEAD 基线验证）**：

| 测试 | 归因（含基线验证） |
|---|---|
| `tests/test_terminal_driver.py::test_core_has_no_adapter_menu_literals` | **已由 MA/审查侧修复（`80205bae`）**：原写死 `len(glob("*.py")) == 9`，现为「9 个 P0 模块必需 + rglob 全量子目录字面量扫描」；审查复跑确认通过。本任务曾在 `430ba7f5` 时点按“9→13 计数过期”如实上报（新模块不含任何禁用字面量），现已关账。 |
| `tests/test_backend_perf_opt.py::test_result_flushes_debounced_blocks_through_read_stdout`、`tests/test_codex_adapter.py::test_cwd_matches_repository_root`、`tests/test_portability_paths.py::test_norm_path_posix_branch`、`tests/test_worker_branch.py::test_steer_worker_*`（3 项） | **既有失败（已基线验证）**：同一命令在 `git archive HEAD`（ed956bb9，不含本任务任何文件）的纯净副本上 **6 failed, 1 passed**（那 1 passed 即 driver 计数测试）——与全量回归失败集完全一致；且这些测试均不 import `core.terminal`。未在本任务引入、未在越界范围修改。 |

**未测 / 未验证（不得当作已解决）**：

1. 旧 build（<26100）`ClosePseudoConsole` 的“等待客户端 drain”行为——实现按官方建议先关输出 +
   有界 worker；该路径**未真机验证**。
2. OS 级 CTRL_C_EVENT：**有界定位已完成（§2.6.1）**——本机/本 build 的安全接口集内 A/B/C/C3 全部不可行；
   R1 保持未验收，待 MA 决策（挂起 / 明示 best-effort 同步文档 / 换环境再验）。Ctrl-D 为 Windows shell
   应用语义，不承诺通用 POSIX EOF。
3. detach 宿主可行性（ambient Job 寿命、`DuplicateHandle` 移交）：不在本模块（计划 §5.1/§7 产品门）。
4. 长时间稳定性、输出洪泛压测、并发多终端、跨 build/跨机型：未做。
5. 真实 OS 故障（CloseHandle 真实失败、旧 build 阻塞）未覆盖；清理失败重试分别用
   “真实收敛预算耗尽”与“包装层注入（已标注非真实 OS 失败）”两路覆盖。
6. POSIX：本模块在非 Windows 明确拒绝；无 POSIX 后端。
7. **r3 输入门端到端**：runtime 的 `_enter_input_gate`/`_close_input_gate`/`_wait_input_settled`
   行为锚定在集成树（r3 core）的 runtime 测试；本工作树按指示未 cherry-pick core（仍为 r2 runtime），
   因此本地只验证**后端侧**保证：写自身有界、interrupt 有界、close 与在途写不竞态、read 不持全局锁。
   集成后建议在集成树复跑 `backend-read-nonblocking` / `backend-write-budget` / `backend-interrupt-bounded`
   三个场景（可直接复用本工作树测试文件）。

---

## 7. 清理与安全核验

| 项目 | 结果 |
|---|---|
| 进程/端口 | 只创建/终止本任务自建进程（临时脚本、ConPTY 子进程、树节点、holder）；未连接、未探测、未终止任何既有服务（`D:\project\Pan` 主进程、mcp servers、qq bot、workbuddy-manager 等）、未触碰 8768 |
| 临时根 | 全部在 `%TEMP%/pan-ta-probe/*` 与 pytest `tmp_path`；测试自建自清 |
| 凭据/网络/模型 | 未使用任何模型、用户凭据、provider；无网络调用 |
| git/进程 | 未 push、未 merge、未 restart 任何服务；未派子代理；未安装全局包；未改依赖锁 |
| 旧证据 | spawn spike 旧 JSON 与摘要**未动**；新增证据仅写入本任务目录 |
| 越界写 | `git status` 仅显示新增文件（无任何现有文件被修改） |

---

## 8. 复现

```bash
# 单文件复跑（含证据）
cd D:/project/pan-worktrees/terminal-backend-implement-20261003
PAN_TERMINAL_EVIDENCE_DIR=audit/terminal/implementation/backend/evidence \
E:/software/miniforge/python.exe -m pytest tests/test_terminal_windows_backend.py -v -p no:cacheprovider

# 仅核心逻辑（跨平台可跑，不依赖 Windows）
E:/software/miniforge/python.exe -m pytest tests/test_terminal_spawn_gate.py::test_env_block_and_command_line_pure_logic -q
```

完整 commit hash 见回执。
