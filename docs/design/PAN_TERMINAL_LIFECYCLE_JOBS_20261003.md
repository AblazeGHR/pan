# Pan Terminal 生命周期、detach 与 Job 运行层探索报告

- 业务任务：T-TERMINAL-PTY-20261003（第一轮并行探索 · 生命周期、detach 与 Job）
- 工作树：`D:/project/pan-worktrees/terminal-lifecycle-explore-20261003`；
  分支 `explore/terminal-lifecycle-20261003`，起点 `35b6fb1`
- 探针目录：`audit/terminal/lifecycle/`（可复现脚本 + `evidence/` 机器可读证据）
- 边界遵守：未操作任何既有服务、Session、Worker、CLI thread 与 8768；未改动其它 TA 树、
  集成树或 `.workflow`；未修改生产模块。所有进程/端口/临时目录均为本探针自建，
  并在 `finally` 中做身份核验清理。不含 secret 读写。
- 本报告区分「实测」（探针输出）与「推断」（源码/文档语义外推，明确标注）。

## 1. 结论速览（全部实测）

| 需求（用户已决定） | 实测结论 | 证据 |
| --- | --- | --- |
| 浏览器/面板断连不改变任何 runtime | 成立。6 次 controller 进程反复断连期间，runner/PTY 根/后台程序 PID 与创建时间不变，未完成命令跨断连继续执行并输出 | `s4_controller_churn` 14/14 |
| 默认终端随 Pan 服务结束 | 两条路径都成立：服务**正常关闭**显式 stop（0.05s 内整树死）；服务**崩溃**由 lease（租约）超时自停（2.0s grace，实测 2.07s） | `s1_default_normal_exit` 11/11、`s2_default_crash` 8/8 |
| 显式 detach 保留原 PTY/PID 与重连 | 成立。detach 后宿主正常退出或崩溃，runner+PTY+后台程序存活且 PID/创建时间完全一致；新 controller 进程重连后 shell 变量与运行中程序状态保留 | `s3_detach_normal_exit` 16/16、`s5_detach_crash_tree` 9/9 |
| 整树清理（含孙进程） | 成立且与 runner 退出方式无关：graceful stop、lease 超时、runner 被硬杀三种路径都在 ≤0.6s 内清空整棵树 | s1/s2/s5 + Job 探针 C/D |
| PID 创建时间核验 | 全场景使用 PID + 精确 100ns FILETIME + 退出码判定；重连/存活/死亡判定均以身份为准，未出现误判或误杀 | 全部 evidence JSON |
| Windows Job Object 无法摘出已有成员 | 成立。加入第二个 Job 不能中和 `KILL_ON_JOB_CLOSE` 绑定；关闭该 Job 句柄仍杀死进程；无摘除 API；逃脱只能在**出生时**用 breakaway | `job_object_probe` A–E 6/6 |
| 与 Pan Job 共享运行层 | 可行，但仅共享 runner/注册表/身份核验/恢复模式；默认寿命策略相反，不能复用其「默认随 Pan 存活」语义 | §3 源码分析、§6 设计 |

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

:: 全场景矩阵（s1–s5，约 1–2 分钟）
%PY% driver.py --scenario all

:: 单场景 / 保留现场
%PY% driver.py --scenario s3_detach_normal_exit --keep

:: Job Object 所有权布局证据
%PY% job_object_probe.py --out evidence/job_object_probe.json
```

输出：`evidence/s*.json`（逐断言 + 进程身份表 + 时间线）、`evidence/summary.json`、
`evidence/job_object_probe.json`、`evidence/environment.json`。运行数据根为
`%TEMP%/pan-term-lifecycle-<ts>/<scenario>/`，场景结束即删（`--keep` 除外）。

### 2.3 本轮输出摘要

- `driver.py --scenario all`：**58/58 断言通过**（s1 11、s2 8、s3 16、s4 14、s5 9）
- `job_object_probe.py`：**6/6 子探针通过**
- 清理核验：所有场景 `cleanup.survivors = []`；无 `pan-term-lifecycle-*`/`pan-term-jobs-*` 临时目录残留；
  全部控制端口停止后连接被拒。

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
- 现状语义 = 「默认终止」：Pan 关机或 Worker 清理显式杀 takeover 终端；同时它**天然不可 detach**
  （Job 成员不可摘出，§4 实测 A）。这正是用户需求 3 中「Pan Job 与 Windows Job Object 分开」的现实注脚。

## 4. Windows Job Object 实测事实（job_object_probe，6/6）

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

## 5. 生命周期探针实测（s1–s5，58/58）

### 5.1 探针布局（推荐布局的最小实现）

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

- **runner 出生即拥有 PTY**：runner 本身**不在**任何服务级 kill-on-close Job 中（否则不可 detach，§4）。
- **树守卫归 runner**：runner 自持 kill-on-close Job；无论 runner 优雅退出、崩溃还是被硬杀，内核都会清树。
- **lease = 默认寿命策略**：supervisor 独占 lease 连接；lease 丢失且未 detach 时 runner 在 grace（本轮 2.0s）后自停。
- **detach = durable 标志**：置位后忽略 lease 丢失，需显式 `stop`；状态落盘 `runtime.json`（可被重启后的 Pan reconcile）。
- **连接与运行时解耦**：controller 断开只关 socket，不触碰 Job 句柄/PTY（防「断开即杀」的反模式）。

### 5.2 场景结果（本轮证据数值）

| 场景 | 关键观测 |
| --- | --- |
| **s1 正常退出（默认终止）** | supervisor `STOP_AND_EXIT` → runner 0.05s 退出；Job active 4→0；PTY 根、tick、后台树全部消失；端口 54583 关闭；`runner_final.stopReason=explicit_stop`；两台 controller 进程先后接入（其中一次断连后重连，shell 变量 `%PXS%` 保留） |
| **s2 崩溃（默认终止 · lease）** | supervisor `os._exit(17)`（无清理）→ lease 断开 → runner **2.072s** 后自停（grace 2.0s）；崩溃到全树死 2.585s；`stopReason=lease_expired` |
| **s3 detach + 宿主正常退出 + 重连** | detach 后 supervisor 正常 `exit 0`（不 stop）；等待超过 grace 后 runner/PTY/tick **PID 与 100ns 创建时间完全一致**；无连接期间 tick 计数 39→49 继续增长；新 controller 进程重连，`echo RESULT:%PXS%` 返回原变量；tasklist 仍列出 tick；显式 `stop` 后 0.10s 内 runner 退出、整树清零、端口关闭；`detachedAtStop=true` |
| **s4 浏览器断连抖动** | 6 个 controller 进程（含「慢命令执行中断开」）轮换：慢命令在无人连接期间完成并在重连后读到；runner 记录连接数 1→6；全程 runner/PTY/tick 身份不变；后台程序持续运行 |
| **s5 detach + 崩溃 + runner 硬杀** | detach 后 supervisor 崩溃，runtime 存活；随后**硬杀 runner 自身**（身份核验后 TerminateProcess）→ PTY 根 0.05s、tick 孙进程 0.00s 死亡（runner 自持 Job 句柄被内核关闭触发 kill-on-close），端口在 0.6s 级轮询内确认关闭。该场景无 `runner_final.json`（硬杀），注册表记录成为待 reconcile 的陈旧记录 |

### 5.3 身份核验方法（本轮全部通过）

- 每个自建进程记录 `(pid, createTimeFiletime[100ns], image, exitCode)`；重连/存活断言用 FILETIME 精确相等；
- 清理只用身份匹配后的 `TerminateProcess`，PID 复用不可能被误杀；
- 补充教训（见 §8）：`OpenProcess` 成功 ≠ 进程存活（句柄未关闭的 terminated 进程仍可通过检查）。

## 6. 语义设计：Terminal 端点语义 vs Pan Job 语义（共享底层、不混同）

| 维度 | Pan Job（现有 `background_jobs`） | Terminal 端点（本探索建议） |
| --- | --- | --- |
| 运行载体 | 独立 runner 进程（detached，无 Job Object） | 独立 runner 进程（detached，**自持** kill-on-close 树守卫 Job） |
| 默认寿命 | 与 Pan 生死无关（durable，reconcile 保留存活 runner） | 随 Pan 结束：正常关闭显式 stop；崩溃 lease 超时自停 |
| 显式长寿命 | 无需（默认已长寿命） | 显式 detach（durable），忽略 lease，显式 stop 终止 |
| 输入/输出 | argv（无 shell）→ 日志文件 | PTY ↔ 控制连接（多 controller、可断连） |
| 注册/发现 | 每 Job JSON + 跨进程锁 + 原子替换 | 每 runtime JSON（pid/创建时间/port/detach 状态），同款原子写 |
| 身份核验 | PID+创建时间（psutil 容差 1s）+ zombie 检查 | PID+精确 FILETIME+退出码（本探针实现，可对齐） |
| 崩溃恢复 | reconcile：存活保留，否则 failed；拒杀不可验证 PID | 同原则：**detached 且身份可验证 → re-attach；否则按陈旧记录处理，不杀不可验证进程**（推断，未实测 Pan 重启） |
| 通知 | queue_pending 终态通知（Session） | 不产生 Pan Job 通知；由终端 UI 状态体现 |
| 端点语义 | `agent_background_start` 等 MCP/API | 全局终端管理（可选关联 Workspace/Session——注册表预留 scope 字段） |

**共享底线**：同一条 runner/注册表/身份核验/恢复代码路径可以服务两者；**必须参数化的是生命周期策略**
（lease 或有或无、detach 开关、IO 形态、通知投影），而不是把 Pan Job 改成终端或反之。

**迁移方案 vs 出生所有权（用户要求考察）**：
- 「先由服务持有 PTY，detach 时迁移给独立 owner」在本机内核语义下**不可行**：不能把已存在进程摘出
  kill-on-close Job（A），也不能靠句柄复制转移绑定（§4 规则 4）；
- 唯一可行形态就是**runner 出生即拥有 PTY**（不进入服务级 Job），服务/浏览器都只是可断开的客户端；
  本探针 s3/s5 证明这条路径满足「同 PID 保留 + 重连 + 整树清理」全部要求；
- 「迁移」仅在不涉及 Job 成员的场景（例如纯 handle 传递的管道/端口）才有意义，对 PTY+进程树不适用。

**反模式（均有实测支撑）**：
1. 把 kill-on-close 句柄的生命周期挂在连接/请求作用域：controller 断开会关闭最后一个句柄 → 整树死（A/C 的行为反向证明）；
2. 把已存在 PTY 进程补 assign 进服务级 Job 作为「保底清理」：从此不可 detach（A）；
3. 用「关闭/丢弃 Job 句柄」实现 detach：那不是 detach，是 kill（C）。

## 7. 接口影响与建议下一步

1. **Terminal runtime 注册表（新增，独立于 background_jobs 注册表）**：字段建议沿用探针
   `runtime.json`：`runtimeId / kind / pid / processCreatedAt(FILETIME) / port|pipe / token / detached /
   detachedAt / leaseGraceSeconds / pty{rootPid, rootCreatedAt, shell} / scope{workspaceId, sessionId} / status / history`。
2. **Pan lifespan 接入点**（下一阶段，非本阶段）：关机时遍历终端注册表，对非 detached 发 `stop` 并等待；
   detached 保留并落盘状态；启动时 reconcile（同 `reconcile_running` 原则）。
3. **与 PR #6 / `_PtySession`**：本报告的 runner 布局可作为其外层生命周期容器；具体替换映射与 EOF/背压契约
   属公共契约 TA 的报告，避免重复承诺。
4. **安全边界（决策门，需 MA/用户确认）**：探针用 loopback + 明文 token；产品化需要命名管道/ACL 或受限 token；
   「detach 后 Pan 重启是否自动 re-attach」「detach 是否需要显式用户授权」属产品决策，不在本轮自行扩大承诺。
5. **明确不做**：未改任何生产模块；未把探针接入真实服务；不把「resume 新进程」算作保留原进程（本探针保留的是同 PID 同 PTY）。

## 8. 失败记录与未验证边界

### 8.1 失败与修正（保留过程记录）

| 问题 | 现象 | 修正 |
| --- | --- | --- |
| uv venv trampoline | `Popen.pid` 与 runner 自报 PID 不一致（`endpoint pid mismatch`） | 用 `__PYVENV_LAUNCHER__` 让 base 解释器单进程运行 |
| 僵尸句柄存活误判 | runner 已退出 20s 仍被 `OpenProcess` 判活 | 存活判定加 `GetExitCodeProcess != 259` |
| supervisor 双线程读 socket | lease guard 线程可能吞掉 stop ack | 移除读线程，仅持有 lease socket |
| driver 返回契约 | `finally` 未返回 evidence 导致 `AttributeError` | 场景函数统一 `return sc.finalize(...)` 并捕获意外异常 |
| job 探针临时目录 | `%TEMP%/pan-term-jobs-*` 未删 | 探针结尾 `rmtree`，复跑验证无残留 |

### 8.2 未验证边界（明确标记）

- 真实 Pan 服务、8768、API/WS 路径：边界禁止，未触碰。
- ConPTY resize/背压/Ctrl+C/EOF drain 等交互语义：属公共契约与 PR6 范围，未测。
- 句柄型 watchdog（`OpenProcess(supervisor_pid)` + `WaitForSingleObject` 作为 lease 替代）只做了设计推演，未实测。
- 嵌套 Job 的完整 API 层级关系、`CreateJobSet`（Windows 新 API）：未穷尽。
- 机器重启/休眠/用户切换后 detach runtime 的可见性：OS 语义下 PTY 不可能跨重启存活，产品边界待定。
- token/ACL/命名管道安全模型：探针非产品级安全实现。

## 9. 文件与提交

本轮提交（仅限本 TA 专属路径）：

- `audit/terminal/lifecycle/probe_lib.py`、`runner.py`、`supervisor.py`、`controller.py`、
  `tick.py`、`driver.py`、`job_object_probe.py`、`README.md`
- `audit/terminal/lifecycle/evidence/*.json`（environment、s1–s5、job_object_probe、summary）
- 本报告 `docs/design/PAN_TERMINAL_LIFECYCLE_JOBS_20261003.md`

完整 commit hash 见提交记录（`git log -- audit/terminal/lifecycle`）与 TA 回执。
