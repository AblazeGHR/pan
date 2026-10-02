# Pan Terminal 生命周期、detach 与 Job 运行层探索报告

- 业务任务：T-TERMINAL-PTY-20261003（第一轮并行探索 · 生命周期、detach 与 Job）
- 工作树：`D:/project/pan-worktrees/terminal-lifecycle-explore-20261003`；
  分支 `explore/terminal-lifecycle-20261003`，起点 `35b6fb1`
- 探针目录：`audit/terminal/lifecycle/`（可复现脚本 + `evidence/` 机器可读证据）
- 边界遵守：未操作任何既有服务、Session、Worker、CLI thread 与 8768；未改动其它 TA 树、
  集成树或 `.workflow`；未修改生产模块。所有进程/端口/临时目录均为本探针自建，
  并在 `finally` 中做身份核验清理。不含 secret 读写。
- 本报告区分「实测」（探针输出）与「推断」（源码/文档语义外推，明确标注）。

## 1. 结论速览（实测与源码推断分级）

> 证据来源分两批，互不覆盖：第一轮提交 `4b20683` 的 `evidence/s1–s5 + job_object_probe`（58/58 + 6/6）；
> 本报告返工后新增 follow-up 运行 `evidence/followup/`（s1–s6 共 67/67、K/H 独立探针），旧证据保持原样。

| 需求（用户已决定） | 结论 | 分级 | 证据 |
| --- | --- | --- | --- |
| 浏览器/面板断连不改变任何 runtime | 成立。6 次 controller 进程反复断连期间，runner/PTY 根/后台程序 PID 与创建时间不变，未完成命令跨断连继续执行并输出 | 实测 | `s4_controller_churn` 14/14 |
| 默认终端随 Pan 服务结束 | 两条路径都成立：服务**正常关闭**显式 stop（0.05s 内整树死）；服务**崩溃**由 lease（租约）超时自停（2.0s grace，实测 2.07s） | 实测 | `s1_default_normal_exit` 11/11、`s2_default_crash` 8/8 |
| 显式 detach 保留原 PTY/PID 与重连 | 成立。detach 后宿主正常退出或崩溃，runner+PTY+后台程序存活且 PID/创建时间完全一致；新 controller 进程重连后 shell 变量与运行中程序状态保留 | 实测 | `s3_detach_normal_exit` 16/16、`s5_detach_crash_tree` 9/9 |
| 整树清理（含孙进程） | 成立且与 runner 退出方式无关：graceful stop、lease 超时、runner 被硬杀三条路径都清空整棵树（Job 句柄最后持有者消失即触发） | 实测 | s1/s2/s5 + Job 探针 C/D/H |
| PID 创建时间核验 | 终止路径改为**单句柄原子核验**（同一 handle 验证 FILETIME+退出码并 Terminate）；核验失败不终止的针对性测试通过 | 实测 | K1–K3、全部 evidence JSON |
| Windows Job Object 成员与句柄所有权 | 成员：assign 可加入运行中进程，**不可移除**、无摘除 API、逃脱只能出生时 breakaway；句柄所有权：可经 DuplicateHandle 跨进程移交，原持有者退出不触发 kill，**最后一个句柄关闭才整树死** | 实测（官方文档语义 + A–F/H 探针） | `job_object_probe` A–E、`job_handle_handoff` H0–H10 |
| 启动失败门禁 | guard Job assign 失败时 fail-closed：不发布 running 端点、宿主不接入、仅清理自有后代后退出（注入验证） | 实测 | `s6_startup_assign_failure` 9/9 |
| 与 Pan Job 共享运行层 | 可行，但仅共享 runner/注册表/身份核验/恢复模式；默认寿命策略相反，不能复用其「默认随 Pan 存活」语义 | **源码推断（§3，未接入真实 Pan）** | §3 源码分析、§6 设计 |

## 2. 环境、版本与复现

### 2.1 环境（本轮实测）

| 项 | 值 |
| --- | --- |
| OS | Windows 11 `10.0.26200.9457`（`platform` 报 `Windows-11-10.0.26200-SP0`；cmd 版本横幅 10.0.26200.9457） |
| Python | 3.14.5（MSC v.1944 64bit），`C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe` |
| uv | 0.9.14 |
| pywinpty | 3.0.5（ConPTY backend，显式 `Backend.ConPTY`） |
| 临时环境 | `C:/Users/14709/AppData/Local/Temp/pan-term-lifecycle-probe-venv`（仅临时 venv，未安装到全局、未改仓库依赖） |
| 控制端点 | 每个 runner 自绑 `127.0.0.1:0`（OS 分配即「已验证空闲」），共享全程仅 loopback；本轮端口 s1=54583、s2=53313、s3=51175、s4=51579、s5=64826，停止后均 refuse |

两个环境陷阱（已修复并记录，见 §8）：

1. uv venv 的 `Scripts/python.exe` 是 trampoline（先起 launcher 再起 base 解释器，`Popen.pid ≠ 子进程 os.getpid()`）。
   探针改用 `__PYVENV_LAUNCHER__`（CPython venv launcher 协议）让 base 解释器单进程保持 venv 前缀，PID 身份精确。
2. 进程已退出但父进程仍持有 Popen 句柄时，Windows 保留 PID，`OpenProcess`/`GetProcessTimes` 仍成功。
   存活判定必须叠加 `GetExitCodeProcess != STILL_ACTIVE(259)`。这也是 Pan `_owns_process` 检查 `STATUS_ZOMBIE` 的同类问题。

另：本探针自身的宿主进程处于一个 ambient Job Object 中（`IsProcessInJob(self, NULL)=true`）。它只影响
breakaway 对照实验的解释（B 组子进程逃出我们的 Job 但仍留在 ambient Job），不影响上述结论。

### 2.2 复现命令（本报告全部步骤均已亲自执行）

```bat
cd audit/terminal/lifecycle
set PY=C:/Users/14709/AppData/Local/Temp/pan-term-lifecycle-probe-venv/Scripts/python.exe

:: 第一轮证据（已提交，除非要重跑，不要覆盖 evidence/ 下同名文件）
%PY% driver.py --scenario all

:: follow-up：全场景矩阵 s1–s6，写入新目录，不覆盖旧证据
%PY% driver.py --scenario all --evidence-dir evidence/followup

:: 单场景 / 保留现场
%PY% driver.py --scenario s6_startup_assign_failure --keep --evidence-dir evidence/followup

:: Job Object 所有权布局证据（A–E）
%PY% job_object_probe.py --out evidence/job_object_probe.json

:: 跨进程 Job 句柄所有权移交（H0–H10）
%PY% job_handle_handoff.py --out evidence/followup/job_handle_handoff.json

:: 单句柄原子核验失败不终止的针对性测试（K1–K3）
%PY% kill_identity_probe.py --out evidence/followup/kill_identity_probe.json
```

输出：`evidence/s*.json`（逐断言 + 进程身份表 + 时间线）、`evidence/summary.json`、
`evidence/job_object_probe.json`、`evidence/environment.json`；follow-up 增量在
`evidence/followup/`（s1–s6、job_handle_handoff、kill_identity_probe、environment、summary）。
**旧提交证据不被 follow-up 覆盖。** 运行数据根为 `%TEMP%/pan-term-lifecycle-<ts>/<scenario>/`，
场景结束即删（`--keep` 除外）。

### 2.3 输出摘要（两批）

- 第一批（commit `4b20683`，`evidence/`）：`driver.py --scenario all` **58/58**（s1 11、s2 8、s3 16、s4 14、s5 9）；`job_object_probe` **6/6**。
- follow-up（`evidence/followup/`）：`driver.py --scenario all` **67/67**（s1–s5 同上 + s6 9）；
  `job_handle_handoff` **11/11**；`kill_identity_probe` **3/3**。
- 清理核验（两批）：所有场景 `cleanup.survivors = []`；无 `pan-term-lifecycle-*`/`pan-term-jobs-*`/`pan-term-kill-*`
  临时目录残留；全部控制端口停止后连接被拒。

## 3. 现有代码事实（只读分析，未修改）

### 3.1 Pan Job（`background_jobs` / `background_runner`）——设计意图是「跨 Pan 存活」

- `packages/core/background_runner.py`（全文件）：独立 runner 进程按 job 记录启动 argv（无 shell），
  写回 `pid` + `processCreatedAt`，日志落盘；Job 记录与 Session 解耦。
- `packages/core/background_jobs.py`：
  - `_spawn_background_runner`（L677-715）用 `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`（L692）派生 runner，
    **不放入任何 Job Object**；子进程组与 Pan 进程树脱钩。
  - 身份核验：`_process_create_time`（L2788，psutil）、`_owns_process`（L2798，创建时间容差 1s + zombie 检查）。
  - 取消：`cancel`（L2812）先验证 PID 身份，再 `_kill_tree`（L2843）后代优先整树终止；身份不可验证时拒杀。
  - 恢复：`reconcile_running`（L3029）——存活 runner 保留；身份缺失/不可用/复用/死亡记为 failed，**绝不对不可验证 PID 下手**。
- 结论（实测无关，源码语义）：Pan Job 是 durable（持久）语义，**默认与 Pan 生死无关**；
  「终端默认随 Pan 结束」与它相反。可共享的是运行层机制，不是默认寿命策略。

### 3.2 服务 lifecycle

- `packages/web/server.py` `lifespan`（L187-274）：关机路径最终 `await worker.shutdown_all()`（L267）；
  启动路径含 recovery（`_initialize_startup_recovery`）与 `background_jobs.start_recovery_loop()`（L219）。
- `main.py`（L345-400）：uvicorn `server.run()` 前后仅管理 QQ/微信 bot（atexit），目前没有终端层。

### 3.3 takeover 的 Windows Job Object 现状（`TakeoverJob`）

- `packages/core/takeover_job.py`：构造时设 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`（0x2000）；
  `launch` 用 `CREATE_NEW_CONSOLE | CREATE_SUSPENDED(=4)` 挂起创建 → `AssignProcessToJobObject` → `NtResumeProcess`
  （L43-59；消除「创建后、入 Job 前」的逃逸窗口）；`stop`（L61-77）`TerminateJobObject` 并轮询 active 归零。
- 接线：`server.py` `_open_takeover_terminal`（L3108）/`api_takeover`（L10857）；
  `worker.py` `_kill_takeover_terminal`（L5541）、`shutdown_all`（L7135，docstring 明示回收 takeover 终端）、
  `_kill_worker_unlocked`（L5725）、`_takeover_worker_unlocked`（L5663）等。
- 现状语义 = 「默认终止」：Pan 关机或 Worker 清理显式杀 takeover 终端；进程成员不可从 Job 摘出（§4 实测 A）。
  若未来要支持所有权移交，句柄所有权可跨进程移交（§4 实测 F/H），但 **ConPTY 宿主/IO 接管未验证**（§8.2），
  不得据此声称 takeover 终端可 detach。这正是用户需求 3 中「Pan Job 与 Windows Job Object 分开」的现实注脚。

## 4. Windows Job Object 实测事实（官方文档 + 三组探针）

### 4.1 官方文档语义（Microsoft Learn）

- **成员只增不减**：“After a process is associated with a job, the association cannot be broken.”
  （[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)）。
  运行中进程可被 `AssignProcessToJobObject` **加入**（也允许嵌套 Job：多个 Job 按父子层次并存）。
- **kill-on-close 由“最后一个句柄”触发**：“…if the job has the JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE flag specified,
  closing the last job object handle terminates all associated processes and then destroys the job object itself.”
  （同上）。
- **Job 句柄可复制**：“Job — The handle is returned by the CreateJobObject function” 属可复制对象类型；
  “The duplicate handle refers to the same object as the original handle.”；复制到其他进程时，句柄值仅在目标进程内有效，
  需 IPC 传递（[DuplicateHandle](https://learn.microsoft.com/en-us/windows/win32/api/handleapi/nf-handleapi-duplicatehandle)）。

### 4.2 探针实测

`job_object_probe`（A–E，6/6）与 `job_handle_handoff`（H0–H10，11/11）：

| 子探针 | 实测事实 |
| --- | --- |
| **A no_extraction** | 子进程 assign 进 kill-on-close Job K（`inK=true`）后，再 assign 进第二个 Job X 成功（嵌套，`inX=true`、`inK` 仍 `true`）；**关闭 K 的句柄后子进程仍被杀**（0.0s），即使 X 仍打开。成员资格只增不减，且 **kill-on-close 绑定不能被“多加一个 Job”中和** |
| **B breakaway_birth_only** | `BREAKAWAY_OK` Job 内的 holder 以 `CREATE_BREAKAWAY_FROM_JOB` 创建子进程：`childInBreakawayJob=false`（出生时逃脱；因 ambient job 存在 `childInAnyJob=true`）。对照：对已存在进程只能 assign「加入」（`assignExistingChildToSameJob=true`），无等价「摘出」 |
| **C owner_death_kills_tree** | holder 建 kill-on-close Job、assign 子进程后 `os._exit(0)`（无清理代码）：内核关闭其句柄，子进程 0.0s 死。**所有者暴死 = 整树死**，无需优雅退出 |
| **D1/D2 生产 TakeoverJob** | 复用 `packages.core.takeover_job.TakeoverJob`（只读、自建子进程）：正常 `stop()` 与 holder 崩溃两条路径子进程都 0.0s 死 |
| **E api_surface** | kernel32 共 1697 个导出中 Job 相关 13 个（Assign/Create/IsProcessInJob/Open/Query/Set/Terminate/FreeMemory/…），**无任何 Remove/Detach 型 API**（`removalLikes=[]`） |
| **F/H 句柄所有权移交**（新增） | P 创建 K 并罩住树（C 及继承的孙进程 G）；`DuplicateHandle` 把 K 句柄复制给独立 holder A，**P 关闭自己的句柄后树存活**（H2，观察 2.0s 无死亡）；A 再把句柄复制给独立 holder B（`HANDOFF_OK`），B 用自己进程内的句柄自证成员关系（`B_HOLDS active=2 member=True`，H4）；**A 关闭句柄并退出后，C/G 的 PID 与 100ns 创建时间完全不变**（H6）；**B 关闭最后一个句柄（B 仍存活）瞬间整树死亡 0.0s**（H7–H9） |

### 4.3 所有权布局规则（修正版）

1. **进程成员资格**：运行中进程可被加入 Job（含嵌套）；**不能从任何 Job 移除**（文档 + A/E）。
   避免继承成员资格只有**出生时**一个时机：`CREATE_BREAKAWAY_FROM_JOB` + 父 Job 的 `BREAKAWAY_OK`（B 实测），
   或父 Job 设 `SILENT_BREAKAWAY_OK`（文档语义，未单独实测）。
2. **kill-on-close 绑定**：一旦进程属于某 kill-on-close Job，该约束不可被“再加一个 Job”抵消（A）。
3. **句柄所有权**：Job 的生命周期绑定在**句柄集合**上，而不是某个特定进程：`DuplicateHandle` 可跨进程共享/移交句柄，
   原持有者关闭自己的句柄或退出**不会**触发 kill，只要仍有其他持有者（F/H2/H6）；
   **最后一个句柄关闭（含持有者崩溃）才终止整树**（文档 + C/H7–H9）。
4. 因此“迁移”要分层表述：
   - **Job 成员资格迁移**（让已存在进程换一个 Job 约束）→ 不可行；
   - **Job 句柄所有权迁移**（谁持有 kill-on-close 句柄）→ **可行且已实测**（DuplicateHandle + IPC 传递句柄值）；
   - **PTY 宿主/IO 接管迁移**（接管 ConPTY 主控、读写出流）→ **未测**，本报告不做任何可行性声明。

| 子探针 | 实测事实（本轮数值） |
| --- | --- |
| **A no_extraction** | 子进程先 assign 进 kill-on-close Job K（`inK=true`），再 assign 进第二个 Job X（该 build 允许嵌套 Job，`inX=true`、`inK` 仍 `true`）；**关闭 K 的句柄后子进程仍被杀**（0.0s），即使 X 仍打开。成员资格只增不减，kill-on-close 绑定无法被「多加一个 Job」中和 |
| **B breakaway_birth_only** | 在 `BREAKAWAY_OK` Job 内的 holder 以 `CREATE_BREAKAWAY_FROM_JOB` 创建子进程：`childInBreakawayJob=false`（出生时逃脱成功；因 ambient job 存在 `childInAnyJob=true`）。对照：对已存在进程 `assign` 只能「加入」（`assignExistingChildToSameJob=true`），没有等价的「摘出」操作 |
| **C owner_death_kills_tree** | holder 建 kill-on-close Job、assign 子进程后 `os._exit(0)`（无任何清理代码）：内核关闭其句柄，子进程 0.0s 内死亡。**所有者暴死 = 整树死**，无需依赖优雅退出 |
| **D1/D2 生产 TakeoverJob** | 直接复用 `packages.core.takeover_job.TakeoverJob`（只读、自建子进程）：正常 `stop()` 路径子进程 0.0s 死；holder 崩溃路径（不调 stop）子进程同样 0.0s 死。生产类行为与设计一致 |
| **E api_surface** | kernel32 共 1697 个导出中 Job 相关 13 个：`AssignProcessToJobObject`、`CreateJobObjectA/W`、`CreateJobSet`、`FreeMemoryJobObject`、`IsProcessInJob`、`OpenJobObjectA/W`、`QueryInformationJobObject`、`QueryIoRateControlInformationJobObject`、`SetInformationJobObject`、`SetIoRateControlInformationJobObject`、`TerminateJobObject`；**无任何 Remove/Detach 型 API**（`removalLikes=[]`） |

由 A–E 得到所有权布局规则（A–E 为实测，末句为文档语义推断）：

1. 进程与 Job 的关系**只能在出生时决定**（breakaway 需要 Job 允许 + 创建标志配合）；
2. 已加入 `KILL_ON_JOB_CLOSE` Job 的进程**无法迁出**，加 Job 不能抵消其约束；
3. `KILL_ON_JOB_CLOSE` Job 的句柄持有者 = 该树的生命周期所有者：最后一个句柄关闭（含持有者崩溃）即整树终止；
4. 句柄复制（DuplicateHandle）只增加引用计数、不「转移」绑定——持有者集合的最后一个关闭仍是终止点（推断，源自 Windows 文档语义；A/C 提供行为佐证）。

## 5. 生命周期探针实测（s1–s6；旧批 58/58，follow-up 67/67）

### 5.1 探针布局（推荐布局的最小实现；寿命探针，非 race-free 生产 spawn）

```
supervisor.py（宿主/Pan 替身，自建进程）
  └─ 派生 runner 为 detached 进程（CREATE_NEW_PROCESS_GROUP|DETACHED_PROCESS，镜像 _spawn_background_runner）
  └─ 持有 lease socket ─────────────┐
controller.py（浏览器替身）─ 控制 socket ─┤
                                       runner.py（PTY runtime owner）
                                         ├─ ConPTY: OpenConsole.exe + cmd.exe（出生后立即全部 assign）
                                         ├─ runner 自持 KILL_ON_JOB_CLOSE Job（罩住 PTY 树，含后代）
                                         └─ 127.0.0.1:<ephemeral> JSON-line 控制端点 + runtime.json 注册表
```

- **runner 出生即拥有 PTY**：runner 本身**不在**任何服务级 kill-on-close Job 中（否则成员不可摘出，§4）。
- **树守卫归 runner**：runner 自持 kill-on-close Job；无论 runner 优雅退出、崩溃还是被硬杀，内核都会清树。
- **lease = 默认寿命策略**：supervisor 独占 lease 连接；lease 丢失且未 detach 时 runner 在 grace（本轮 2.0s）后自停。
- **detach = durable 标志**：置位后忽略 lease 丢失，需显式 `stop`；状态落盘 `runtime.json`（可被重启后的 Pan reconcile）。
- **连接与运行时解耦**：controller 断开只关 socket，不触碰 Job 句柄/PTY（防「断开即杀」的反模式）。
- **启动门禁（fail-closed）**：spawn 后逐个核对 PTY 后代的 guard-Job assign 结果；任一失败即不发布
  running 端点、不监听控制端口、只做自有进程身份核验清理后非零退出（s6 注入验证）。
- **明确限制**：本探针是**寿命/所有权语义探针，不是 race-free 生产 spawn**。PTY 由 `pywinpty.spawn` 创建后
  才 snapshot/assign，启动窗口内新孙进程理论上可能逃逸 Job 守卫（推断：ConPTY 交互 shell 在收到输入前不产生
  用户后代，窗口实际很小，但未做确定性逃逸实验）；`pywinpty.spawn` 不暴露 creationflags，
  无法用“挂起创建→assign→恢复”（生产 `TakeoverJob` 的做法）。
  生产 backend 若要消除该窗口，需支持挂起式 spawn 或接受残余风险——列为公共 backend 实现前置（§7.5、§8.2）。

### 5.2 场景结果（本轮证据数值）

| 场景 | 关键观测 |
| --- | --- |
| **s1 正常退出（默认终止）** | supervisor `STOP_AND_EXIT` → runner 0.05s 退出；Job active 4→0；PTY 根、tick、后台树全部消失；端口 54583 关闭；`runner_final.stopReason=explicit_stop`；两台 controller 进程先后接入（其中一次断连后重连，shell 变量 `%PXS%` 保留） |
| **s2 崩溃（默认终止 · lease）** | supervisor `os._exit(17)`（无清理）→ lease 断开 → runner **2.072s** 后自停（grace 2.0s）；崩溃到全树死 2.585s；`stopReason=lease_expired` |
| **s3 detach + 宿主正常退出 + 重连** | detach 后 supervisor 正常 `exit 0`（不 stop）；等待超过 grace 后 runner/PTY/tick **PID 与 100ns 创建时间完全一致**；无连接期间 tick 计数 39→49 继续增长；新 controller 进程重连，`echo RESULT:%PXS%` 返回原变量；tasklist 仍列出 tick；显式 `stop` 后 0.10s 内 runner 退出、整树清零、端口关闭；`detachedAtStop=true` |
| **s4 浏览器断连抖动** | 6 个 controller 进程（含「慢命令执行中断开」）轮换：慢命令在无人连接期间完成并在重连后读到；runner 记录连接数 1→6；全程 runner/PTY/tick 身份不变；后台程序持续运行 |
| **s5 detach + 崩溃 + runner 硬杀** | detach 后 supervisor 崩溃，runtime 存活；随后**硬杀 runner 自身**（身份核验后 TerminateProcess）→ PTY 根 0.05s、tick 孙进程 0.00s 死亡（runner 自持 Job 句柄被内核关闭触发 kill-on-close），端口在 0.6s 级轮询内确认关闭。该场景无 `runner_final.json`（硬杀），注册表记录成为待 reconcile 的陈旧记录 |
| **s6 启动 assign 失败门禁**（follow-up 新增） | 注入 `cmd.exe` 的 guard-Job assign 失败：`runtime.json` 仅写 `status=startup_failed`（`port=null`），supervisor 未建立 lease/ready 即退出（rc=4，`mode=startup_failed_assign`）；runner 清理自有后代后退出（`leftoverAfterSweep=[]`）；PTY 根进程已消失；无任何控制端点可连 |

### 5.3 身份核验方法（返工修正）

- 每个自建进程记录 `(pid, createTimeFiletime[100ns], image, exitCode)`；重连/存活断言用 FILETIME 精确相等。
- **终止路径改为单句柄原子核验**（`kill_verified_detail`）：`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION |
  PROCESS_TERMINATE)` 只调用一次，FILETIME 校验、`GetExitCodeProcess != STILL_ACTIVE` 存活校验与
  `TerminateProcess` 全部作用于**同一 handle**。该 handle 钉住一个进程对象：若 PID 在打开后被复用，
  被校验/被终止的仍是打开时的对象，PID 复用不会把终止落到新进程上。此前的实现（先在 A handle 校验、
  再 `OpenProcess(PROCESS_TERMINATE)` 终止）存在 check-then-kill 的 TOCTOU 窗口，报告原文「PID 复用不可能
  被误杀」措辞过强，现已删除。
- 针对性负例（`kill_identity_probe`，3/3）：K1 伪造 +1 FILETIME → 拒绝且进程存活；K2 已退出但 PID 仍可寻址
  （父进程持有句柄）→ 判定 not-running、不终止；K3 同句柄正确身份 → 终止成功。
- 观测类断言（`identity_matches`）仍分别打开 handle 读 FILETIME 与退出码——仅用于只读判定，不用于终止。

## 6. 语义设计：Terminal 端点语义 vs Pan Job 语义（共享底层、不混同）

| 维度 | Pan Job（现有 `background_jobs`） | Terminal 端点（本探索建议） |
| --- | --- | --- |
| 运行载体 | 独立 runner 进程（detached，无 Job Object） | 独立 runner 进程（detached，**自持** kill-on-close 树守卫 Job） |
| 默认寿命 | 与 Pan 生死无关（durable，reconcile 保留存活 runner） | 随 Pan 结束：正常关闭显式 stop；崩溃 lease 超时自停 |
| 显式长寿命 | 无需（默认已长寿命） | 显式 detach（durable），忽略 lease，显式 stop 终止 |
| 输入/输出 | argv（无 shell）→ 日志文件 | PTY ↔ 控制连接（多 controller、可断连） |
| 注册/发现 | 每 Job JSON + 跨进程锁 + 原子替换 | 每 runtime JSON（pid/创建时间/port/detach 状态），同款原子写 |
| 身份核验 | PID+创建时间（psutil 容差 1s）+ zombie 检查 | PID+精确 FILETIME+退出码；**终止走单句柄原子核验**（本探针实现，可对齐） |
| 崩溃恢复 | reconcile：存活保留，否则 failed；拒杀不可验证 PID | 同原则：**detached 且身份可验证 → re-attach；否则按陈旧记录处理，不杀不可验证进程**（推断，未实测 Pan 重启） |
| 通知 | queue_pending 终态通知（Session） | 不产生 Pan Job 通知；由终端 UI 状态体现 |
| 端点语义 | `agent_background_start` 等 MCP/API | 全局终端管理（可选关联 Workspace/Session——注册表预留 scope 字段） |

**共享底线**：同一条 runner/注册表/身份核验/恢复代码路径可以服务两者；**必须参数化的是生命周期策略**
（lease 或有或无、detach 开关、IO 形态、通知投影），而不是把 Pan Job 改成终端或反之。

**迁移方案 vs 出生所有权（返工修正；按三个层次分别结论）**：
- **Job 成员资格迁移**（让已存在进程改属另一个 Job 约束）：不可行——成员不可摘出（A/E + 官方文档），
  assign 只能“加入”，嵌套 Job 只能增加约束；
- **Job 句柄所有权迁移**（kill-on-close 句柄由谁持有）：**可行且已实测**。`DuplicateHandle` 可把句柄复制给
  独立 holder（句柄值经 IPC 传递），原持有者关闭自己句柄/退出不触发 kill，最后一个句柄关闭才整树终止（F/H）。
  这意味着“服务 → 独立 owner”的守卫职责移交在 Job 层面是可以设计的；
- **PTY 宿主/IO 接管迁移**（把 ConPTY 主控、生命周期与读写流转交给另一个进程）：**未测**。ConPTY 句柄/通信
  通常为进程本地（pywinpty 未暴露跨进程迁移接口），本报告不对此做可行性判断，需要时另行探针。
- 综合建议不变但不再是“唯一可行”：**runner 出生即拥有 PTY（不进入服务级 Job）**仍是最简单解——
  无移交协议、单一寿命 owner、s3/s5 已验证「同 PID 保留 + 重连 + 整树清理」；句柄移交方案（F）作为
  “守卫句柄需要由服务交给其他进程”的场景备选，其产品价值取决于上述 PTY 宿主/IO 接管是否成立。

**反模式（均有实测支撑）**：
1. 把 kill-on-close 句柄的生命周期挂在连接/请求作用域：controller 断开会关闭最后一个句柄 → 整树死（A/C 反向证明）；
2. 把已存在 PTY 进程补 assign 进服务级 Job 作为「保底清理」：从此该进程不可摘出（A）；
3. 在没有其他持有者时用「关闭/丢弃 Job 句柄」实现 detach：那是 kill，不是 detach（C/H7–H9）；
4. 把句柄移交（F）误当成“进程迁移”：移交只改变谁持有 kill-on-close 句柄，不改变进程的 Job 成员关系，
   也不接管 PTY 读写。

## 7. 接口影响与建议下一步

1. **Terminal runtime 注册表（新增，独立于 background_jobs 注册表）**：字段建议沿用探针
   `runtime.json`：`runtimeId / kind / pid / processCreatedAt(FILETIME) / port|pipe / token / detached /
   detachedAt / leaseGraceSeconds / pty{rootPid, rootCreatedAt, shell} / scope{workspaceId, sessionId} / status / history`。
2. **Pan lifespan 接入点**（下一阶段，非本阶段）：关机时遍历终端注册表，对非 detached 发 `stop` 并等待；
   detached 保留并落盘状态；启动时 reconcile（同 `reconcile_running` 原则）。
3. **与 PR #6 / `_PtySession`**：本报告的 runner 布局可作为其外层生命周期容器；具体替换映射与 EOF/背压契约
   属公共契约 TA 的报告，避免重复承诺。
4. **detach 语义已由用户授权，不再是待决产品门**：detach 是显式用户操作，重连是其固有环节；
   注册/发现机制（每 runtime JSON + PID/FILETIME 身份核验 + 原子写）属可直接实施的技术决定，
   follow-up 已提供可运行样例。**本轮不作为产品门提出的项**：是否允许 detach、是否额外授权、浏览器自动重连。
5. **公共 backend 实现前置（新增）**：guard-Job 入组需消除“先 spawn 后 assign”的启动窗口（挂起式 spawn、
   原子入组），或明确接受残余逃逸风险并按 s6 的 fail-closed 语义处理 assign 失败；错误注入门禁行为定义见 s6。
6. **仍需工程加固的项**：控制端点使用命名管道/ACL 或受限 token（探针为 loopback+明文 token，非产品安全模型）；
   机器重启/休眠后 detached runtime 的可见性属 OS 事实限制，产品需知晓边界。
7. **明确不做**：未改任何生产模块；未把探针接入真实服务；不把「resume 新进程」算作保留原进程（本探针保留的是同 PID 同 PTY）。

## 8. 失败记录与未验证边界

### 8.1 失败与修正（保留过程记录）

| 问题 | 现象 | 修正 |
| --- | --- | --- |
| uv venv trampoline | `Popen.pid` 与 runner 自报 PID 不一致（`endpoint pid mismatch`） | 用 `__PYVENV_LAUNCHER__` 让 base 解释器单进程运行 |
| 僵尸句柄存活误判 | runner 已退出 20s 仍被 `OpenProcess` 判活 | 存活判定加 `GetExitCodeProcess != 259` |
| supervisor 双线程读 socket | lease guard 线程可能吞掉 stop ack | 移除读线程，仅持有 lease socket |
| driver 返回契约 | `finally` 未返回 evidence 导致 `AttributeError` | 场景函数统一返回 `sc.finalize(...)` 并捕获意外异常（follow-up 改为 try/except + 顺序清理，消除 `return` in `finally` 警告） |
| job 探针临时目录 | `%TEMP%/pan-term-jobs-*` 未删 | 探针结尾 `rmtree`，复跑验证无残留 |
| **kill 路径 TOCTOU（review 返工）** | `kill_verified` 先在 A handle 核验、再新开 `PROCESS_TERMINATE` handle 终止；检查与终止之间 PID 可被复用 | 改为 `kill_verified_detail`：单次 `OpenProcess(QUERY_LIMITED_INFORMATION|TERMINATE)`，同一 handle 完成 FILETIME、退出码核验与终止；K1–K3 负例验证“核验失败不终止”；报告删除“PID 复用不可能被误杀”的绝对措辞 |
| **句柄移交探针断言 bug** | H6 因期望表按角色字母而非 PID 键存放而失败，同时暴露 H2 为假通过（死亡探测恒真） | 修正为 PID 键期望表后重跑：H2/H6 均真实通过（树在 2.0s 观察窗内存活、同 PID/创建时间） |
| kill 探针临时目录 | `%TEMP%/pan-term-kill-*` 未删 | 探针结尾 `rmtree`，复跑验证无残留 |

### 8.2 未验证边界（明确标记）

- 真实 Pan 服务、8768、API/WS 路径：边界禁止，未触碰。
- **PTY 宿主/IO 接管迁移**（ConPTY 主控/读写流转交）：未测，不声称可行（§4.3/§6）。
- **spawn 启动窗口**：`pywinpty.spawn` 后 snapshot/assign 之间理论存在孙进程逃逸窗口；未构造确定性逃逸实验，
  真机交互 shell 在收到输入前不产生用户后代（窗口很小）。s6 只覆盖“assign 失败”门禁，不覆盖“逃逸成功”。
- ConPTY resize/背压/Ctrl+C/EOF drain 等交互语义：属公共契约与 PR6 范围，未测。
- 句柄型 watchdog（`OpenProcess(supervisor_pid)` + `WaitForSingleObject` 作为 lease 替代）只做了设计推演，未实测。
- 嵌套 Job 的完整 API 层级关系、`CreateJobSet`（Windows 新 API）：未穷尽；ambient Job 在本机存在
  （`driverInAnyJob=true`），assign 结论在嵌套环境下取得，非嵌套环境未单独覆盖。
- 机器重启/休眠/用户切换后 detach runtime 的可见性：OS 语义下 PTY 不可能跨重启存活，产品边界待定。
- token/ACL/命名管道安全模型：探针非产品级安全实现。

## 9. 文件与提交

两批提交（均仅限本 TA 专属路径）：

- 第一轮 `4b20683`：`probe_lib.py`、`runner.py`、`supervisor.py`、`controller.py`、`tick.py`、
  `driver.py`、`job_object_probe.py`、`README.md`、`evidence/`（environment、s1–s5、job_object_probe、summary）、本报告。
- follow-up（本次）：新增 `job_handle_handoff.py`、`handoff_role.py`、`kill_identity_probe.py`；
  修改 `probe_lib.py`（单句柄原子核验 + DuplicateHandle helpers）、`runner.py`（per-assign 门禁 + 失败注入）、
  `supervisor.py`（失败门禁接线）、`driver.py`（s6 + `--evidence-dir`）、`README.md`；
  新增 `evidence/followup/`（s1–s6、job_handle_handoff、kill_identity_probe、environment、summary），
  **不覆盖第一轮证据**；本报告同批修订。

完整 commit hash 见提交记录（`git log -- audit/terminal/lifecycle`）与 TA 回执。
