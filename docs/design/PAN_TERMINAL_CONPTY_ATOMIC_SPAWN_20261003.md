# Pan Terminal：生产 ConPTY 原子 spawn 可行性（Windows ctypes spike）

- 任务：`T-TERMINAL-PTY-20261003/windows-spawn/spike/1`
- 工作树：`D:/project/pan-worktrees/terminal-codex-explore-20261003`，
  分支 `explore/terminal-codex-20261003`
- 允许路径（本轮全部产出均在其中）：`audit/terminal/codex/spawn/` 与本文档
- 结论：**方案在真机成立**。首版实施计划 §5.3 的首选方案
  （`CreatePseudoConsole` → `STARTUPINFOEX` + `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` →
  `CreateProcessW(CREATE_SUSPENDED|EXTENDED_STARTUPINFO_PRESENT)` →
  `AssignProcessToJobObject` → `ResumeThread`）已按门禁四要素实测通过，
  **不需要**降级为"先 spawn 再 assign"。
- 证据：`audit/terminal/codex/spawn/evidence/`（7 个场景 + `summary.json`），
  本轮 **52/52 断言通过**。

> 边界遵守：未修改任何生产模块；未合并/推送；未触碰既有服务、Session、Worker、
> CLI thread、8768 或既有 app-server daemon；未使用模型或用户凭据；
> 所有进程/端口/临时目录均为本 spike 自建并在 `finally` 中清理。
> 不含任何"按 PID 直接杀"的路径（无 `taskkill`、无 WMI/CIM 终止）。

---

## 0. 结论速览（实测 / 推断 / 未验证 分级）

| 门禁要素 / 问题 | 结论 | 分级 | 证据 |
|---|---|---|---|
| `atomic_with_spawn`：子进程在**从未执行过**的状态下被 assign 进 guard Job | 成立。挂起期 2.5s 内子进程的启动标记文件始终不存在；assign 后 `IsProcessInJob(child, guardHandle)=True` | 实测 | s1 `no_execution_while_suspended`、`assigned_to_guard_before_resume` |
| `assigned`：guard Job 内确有成员 | 成立。`QueryInformationJobObject` 报 `active_processes=1` | 实测 | s1 `guard_job_reports_member` |
| `identity`：PID + 原始 FILETIME | 成立。FILETIME 从**保留的进程 handle** 读取（4096 位整数，未经过 PID 重开） | 实测 | s1 `identity_captured_from_retained_handle` |
| `handle_bound_for_cleanup`：句柄绑定清理 | 成立。关闭最后一个 guard 句柄后整树消失，`survivors=[]` | 实测 | s1、s4 |
| 恢复执行后真实运行、UTF-8 尾输出与退出码 | 成立。CJK + 符号经 ConPTY 原样回传；`TAIL_MARKER` 到达；退出码 7 保持 | 实测 | s1 |
| `ResizePseudoConsole` 到达被托管应用 | 成立。子进程自报 `CONSOLE_SIZE 100x30` → `132x43` | 实测 | s2 |
| assign 失败时 fail-closed | 成立。**不 resume**、不发布端点、guard 无成员、只清理自建子进程、标记文件始终不存在 | 实测 | s3 |
| runner/holder 被硬杀后整树消失 | 成立。子进程与孙进程都消失，且 holder **没有机会运行任何清理代码** | 实测 | s4 |
| 阻塞 IO reader 的收敛 | `CancelSynchronousIo` 是**唯一可靠**路径（0.0s，`ERROR_OPERATION_ABORTED=995`）；**关闭读端 handle 不能及时中止挂起读** | 实测 | s5 |
| 本机 ambient Job | 宿主进程处于 ambient Job 中；**该 Job 不允许 breakaway**（`CREATE_BREAKAWAY_FROM_JOB` 返回 `ERROR_ACCESS_DENIED=5`） | 实测 | s6 |
| ambient Job 存在时能否嵌套 assign 自己的 guard Job | **能**。`assign_last_error=0`，子进程同时属于 guard 与 ambient 链 | 实测 | s6 |
| ConPTY 是否"因为 takeover 那条路成功所以必然成功" | **不作为依据**。本轮全部结论来自本机对 ConPTY 序列的直接实测 | — | 全文 |
| 跨平台 / POSIX | 未验证（本轮只做 Windows） | 未验证 | — |
| 长时间运行、背压、`ClosePseudoConsole` 的死锁边界 | 未验证，见 §7 | 未验证 | — |

---

## 1. 官方原文依据

本轮先读 Microsoft 主源，不据 `takeover_job.py` 之类的同类实现外推 ConPTY 行为。
以下为实际打开的官方页面及其被引用的语义：

**Creating a Pseudoconsole session**
<https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session>
- 必须先建两条**同步**通信管道，再 `CreatePseudoConsole`，再建进程。
- "Upon completion of the **CreateProcess** call … the handles given during creation
  should be freed from this process. This will decrease the reference count on the
  underlying device object and allow I/O operations to properly detect a broken channel
  when the pseudoconsole session closes its copy of the handles."
- 必须设 `EXTENDED_STARTUPINFO_PRESENT`；属性用 `UpdateProcThreadAttribute(...,
  PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, hpc, sizeof(hpc), NULL, NULL)`。
- "To prevent race conditions and deadlocks, we highly recommend that each of the
  communication channels is serviced on a separate thread…"
- 托管进程若拿到无效的伪控制台句柄、或会话在它启动时被关闭，客户端会弹
  `0xc0000142` 对话框。
- `ClosePseudoConsole` 会终止附着其上的客户端应用；若原始子进程是 shell 型，
  树上的相关进程也会被终止。

**CreatePseudoConsole**
<https://learn.microsoft.com/en-us/windows/console/createpseudoconsole>
- 返回 HRESULT（成功 `S_OK`）；"The handle created by this function must be closed with
  **ClosePseudoConsole** when operations are complete."
- 输入/输出流是 UTF-8 纯文本夹带 VT 序列；`PSEUDOCONSOLE_INHERIT_CURSOR` 需异步应答，
  否则可能 hang。

**ClosePseudoConsole**
<https://learn.microsoft.com/en-us/windows/console/closepseudoconsole>
- 关闭会给仍连着的客户端发 `CTRL_CLOSE_EVENT`；在此期间它们仍可能继续写输出，
  因此应"先关闭输出管道，或继续读直到 `ClosePseudoConsole` 返回"。
- **"Starting Windows 11 24H2 (build 26100) ClosePseudoConsole will return immediately
  to avoid accidental deadlocks. Earlier versions will wait indefinitely…"**
  → 本机 build 26200 属于"立即返回"分支（实测 `close_seconds=0.0`）。

**AssignProcessToJobObject**
<https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject>
- `hProcess` 需要 `PROCESS_SET_QUOTA | PROCESS_TERMINATE`。
- "If the process is already associated with a job, the job specified by *hJob* must be
  empty or it must be in the hierarchy of nested jobs to which the process already belongs,
  and it cannot have UI limits set."
- `JOB_OBJECT_SECURITY_ONLY_TOKEN` 场景要求"the process must be created suspended.
  To create a suspended process, call the CreateProcess function with the
  **CREATE_SUSPENDED** flag."
- "By default, all child processes are associated with the immediate job and every job in
  the parent job chain."

**Job Objects**
<https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects>
- "After a process is associated with a job, the association cannot be broken."
- "…if the job has the JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE flag specified, closing the
  last job object handle terminates all associated processes and then destroys the job
  object itself."
- 子进程默认加入 Job；仅当 Job 设了 `JOB_OBJECT_LIMIT_BREAKAWAY_OK`（且子进程用
  `CREATE_BREAKAWAY_FROM_JOB` 创建）或 `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK` 时才不加入。
- **关键警告（与本机 ambient Job 直接相关）**：若两个 breakaway 极限都不设，
  "if a child process attempts to associate itself or another child process with a job by
  calling AssignProcessToJobObject, the call will fail."

**ResumeThread**
<https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread>
- 递减挂起计数；成功返回**先前的挂起计数**，失败返回 `(DWORD)-1`。
  "If the return value is 1, the specified thread was suspended but was restarted."

---

## 2. 实测环境（可复现）

| 项 | 值 |
|---|---|
| OS | Windows 11，build **26200**.9457（DisplayVersion 25H2） |
| Python | 3.12.12（conda-forge, MSC v.1944, AMD64）— `E:/software/miniforge/python.exe` |
| 依赖 | **无第三方依赖**：只用 `ctypes` + 标准库（不装 pywinpty、不建 venv） |
| ConPTY API | `CreatePseudoConsole` / `ResizePseudoConsole` / `ClosePseudoConsole` 均可用（kernel32 导出实测存在） |
| 每次运行的临时根 | `%TEMP%/conpty-spawn-<scenario>-<rand>/`，场景结束删除 |
| 提交的子进程 | 只有本 spike 自己创建的 `child_probe.py` / `holder.py` |
| 网络 | 不使用；不连接任何既有 daemon 或服务 |

---

## 3. 实测结果

### s1 `s1_atomic_suspended`（15/15）

一次完整的原子 spawn 与恢复：

| 观测 | 值 |
|---|---|
| 子进程 | pid `46020`，raw FILETIME `134354370138654871`（由保留的 `hProcess` 读取） |
| assign 结果 | `child_in_guard_job = True`，`assign_last_error = 0` |
| guard Job 成员 | `active_processes = 1` |
| 挂起期间（2.5s 窗口） | 启动标记文件**不存在**；进程存活但未执行 |
| `ResumeThread` 返回值 | `1`（= "was suspended but was restarted"，与官方语义一致） |
| 恢复后 | 标记文件出现；ConPTY 收到 `UNICODE 中文测试 ✓ αβγ — utf8-roundtrip`、`TAIL_MARKER end-of-output` |
| 退出码 | `7`（与子进程请求一致） |
| 关闭 guard 句柄后 | 存活探针 `survivors = []` |
| 各阶段耗时 | 建管道 0.03 ms、`CreatePseudoConsole` 7.84 ms、`CreateProcessW` 3.07 ms、`assign` **0.03 ms** |

`assign` 只有 0.03 ms —— 这就是"原子"的意义：从进程创建到入组之间，子进程始终是挂起状态，
从未有机会执行代码，因此窗口内不可能产生逃逸后代。

### s2 `s2_resize`（4/4）

`ResizePseudoConsole(hpc, {132,43})` 返回 `S_OK`；子进程在自身运行期间重新查询并打印
`CONSOLE_SIZE 132x43`（初始为 `100x30`）。即 resize 确实到达**被托管应用**，
不只是我们自己的结构体。

### s3 `s3_assign_failure`（7/7，fail-closed 门禁）

注入 assign 失败后：

- 抛出 `SpawnDenied(stage="assign_guard_job")`，`assign_last_error = 6`（`ERROR_INVALID_HANDLE`）；
- `resumed = false` —— **子进程从未恢复执行**；
- 等 2s 后启动标记文件**仍不存在**（证明"不 resume"是真实效果，不是时序巧合）；
- guard Job `active_processes = 0`（没有任何成员被留下）；
- 被拒绝的子进程已被 spawn 路径自己终止：`open_ok=false`、`last_error=87`（进程不存在）。

这对应计划 §5.3 第 3 条：assign 失败/证据不齐 → 不发布 running、仅清理自有后代、非零退出。

### s4 `s4_holder_hardkill`（7/7）

`holder.py` 复刻计划 §5.1 的"布局 B"：holder 进程出生即拥有 PTY、自持
`KILL_ON_JOB_CLOSE` guard Job，且在被硬杀前不运行任何清理。

| 观测 | 值 |
|---|---|
| holder / 子进程 / 孙进程 | pid 34188 / 子进程在 guard 内；孙进程 pid 41268 |
| guard 成员数（ready 时） | 2（子进程 + 孙进程） |
| 硬杀方式 | `kill_verified`：同一 handle 内比对 FILETIME 完全相等 → `TerminateProcess`，`reason="terminated"` |
| 硬杀 3s 后 | `survivors = []` —— 子进程与孙进程**全部消失** |
| holder 自身 | `still_active=false`，退出码 `0xDEAD` |

这是"runner 崩溃/被硬杀 → 整树死"的直接证据，且**不依赖任何清理代码**：
holder 是被强杀的，内核关闭其最后一个 guard 句柄时触发 kill-on-close。

### s5 `s5_reader_cancel`（9/9）

两条收敛路径对比（这是本轮最有价值的一条**负面**结论）：

| 路径 | 收敛结果 | 错误码 | 耗时 |
|---|---|---|---|
| **A：`CancelSynchronousIo(readerThread)`** | 收敛 | `995` (`ERROR_OPERATION_ABORTED`) | **0.0 s** |
| **B：关闭输出读端 handle** | 最终收敛 | `6` (`ERROR_INVALID_HANDLE`) | **6.086 s**（仅在子进程自行退出、写端消失之后） |

路径 B 的 6.086s 不是"慢"的问题：子进程在关闭 handle 后约 6s 才自行退出，读取正是**在那时**
才失败。也就是说，**关闭读端 handle 并不会中止一个已经挂起的同步 `ReadFile`** ——
挂起的 I/O 让 handle 保持被引用。因此后端必须用 `CancelSynchronousIo`，
不能把"关 handle"当作取消手段。

### s6 `s6_ambient_breakaway`（6/6，本机边界如实记录）

| 观测 | 值 |
|---|---|
| 宿主进程是否在 ambient Job 中 | **是**（`in_any_job=true`） |
| 普通子进程（无 breakaway 标志） | 创建成功，`in_any_job=true`（在 ambient 链内） |
| **`CREATE_BREAKAWAY_FROM_JOB` 子进程** | **创建失败**：`last_error=5` / `ERROR_ACCESS_DENIED` |
| 本进程自建 guard Job 的嵌套 assign | **成功**：`assign_last_error=0`，`child_in_guard_job=true`，且 `child_in_ambient_job=true` |

两条要点：

1. **本机 ambient Job 不允许 breakaway**（对应官方"若 Job 未设任一 breakaway 极限，
   子进程的 `AssignProcessToJobObject` 调用会失败"的那段警告在本机是生效环境）。
   因此在这台机器上，**任何进程都无法逃出 ambient 链**——包括我们的终端树。
2. 尽管如此，**把挂起子进程嵌套 assign 进我们自己的 kill-on-close guard Job 依然成功**，
   官方要求的"目标 Job 为空（或已在嵌套层级中）"条件得到满足。
   这意味着树守卫策略在本机可用；同时也意味着存在一个**上层 ambient Job 的约束**，
   其生命周期行为不由 Pan 控制（见 §7 未验证项）。

### s7 `s7_ctypes_traps`（4/4）

把构建过程中踩到的 ctypes 层陷阱固化成可复现证据：

- **`GetProcessTimes` 传 `NULL` 作为可选出参 → 访问违例**：
  `OSError('exception: access violation writing 0x0000000000000000')`。
  Win32 契约允许这些参数为 NULL，但通过 ctypes 在本机调用时会 AV（即使进程句柄有效）。
  修法是四个 `FILETIME` 全部给真实指针；本 spike 的 `read_creation_filetime()` 即如此实现。
- 结构体尺寸自检符合 Win32 ABI：`STARTUPINFOW=104`、`STARTUPINFOEXW=112`、
  `lpAttributeList` 偏移 `104`、`PROCESS_INFORMATION=24`、
  `JOBOBJECT_EXTENDED_LIMIT_INFORMATION=144`。

---

## 4. 构建过程中发现并修正的三个关键问题（保留过程记录）

这三条都不是"从 takeover 类推"能得到的，必须实测；写进这里是因为后端实现会踩同样的坑。

1. **伪控制台建好 ≠ 子进程 std 句柄走 ConPTY（最隐蔽的一个）**
   只设 `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` 时，实测：
   子进程的**控制台**确实是伪控制台（子进程写 `CONOUT$` 能到达 pty），
   但它的 **stdout/stderr 仍指向父进程的句柄**（父进程的输出被继承并"盖住"了 pty 的句柄）。
   症状是"pty 只吐了 16 字节初始化序列、子进程输出跑到宿主控制台"。
   隔离实验（`CONOUT$` 与 stdout 分开打标记，见 §3 s1 之前的 flag 矩阵）显示：
   设 `STARTF_USESTDHANDLES` 且三个 std 句柄置 `NULL` 后，子进程的 stdout 才落到 pty。
   **这与 `bInheritHandles` 取真取假无关**，只与 `STARTF_USESTDHANDLES` 有关。
   → 本 spike 的 `spawn_win.py` 已固定该设置。

2. **`CreatePseudoConsole` 用过的两个 handle 必须在 `CreateProcess` 之后由本进程释放**
   不释放会额外持有底层设备对象引用，导致伪控制台关闭后输出管道**永远读不到 EOF / 断链**。
   官方样本文中明写这一步，但很容易漏。

3. **关闭读端 handle 不能取消挂起的 `ReadFile`**（§3 s5）。收尾必须有界：
   先 `CancelSynchronousIo`，失败再关句柄，且各步都带超时。

另外记录一个过程事实：早期一版调试脚本在收尾时用**无超时**的 `ReadFile` 排空管道，
直接挂死（人工终止）。这也从反面说明"每个阻塞调用都要有上界"是该模块的硬要求，
现版本所有等待均有显式超时。

---

## 5. 对外接口草案（供后续 backend 使用，本轮不实现 backend）

`spawn_win.py` 目前是一个可运行 spike，其形状可以直接收敛成后端原语。
建议的最小接口面：

```python
class GuardJob:
    """JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 树守卫。最后一个句柄关闭即整树终止。"""
    def __init__(self, name: str | None = None) -> None: ...
    raw_handle: int
    def assign(self, hprocess: int) -> tuple[bool, int]: ...   # (ok, GetLastError)
    def active_processes(self) -> int | None: ...              # 只读查询
    def terminate(self, exit_code: int = 1) -> bool: ...
    def close(self) -> None: ...                               # 可能是"整树终止"那一下

class SpawnRecord:            # 计划 §5.4 的 TerminalRecord 从这里取值
    pid: int
    creation_filetime: int     # 原始 100ns，不经转换比较
    command_line: str
    cols: int; rows: int
    assign_ok: bool
    assign_last_error: int
    resumed: bool
    child_in_guard_job: bool
    child_in_ambient_job: bool
    ambient_job_before_spawn: bool
    timings: dict

class ConPtySession:
    @classmethod
    def spawn(cls, command_line, cwd, cols, rows, guard, *,
              inject_assign_failure=False, resume=True) -> "ConPtySession":
        """原子：挂起创建 → 入 guard（失败即 SpawnDenied，绝不 resume）→ resume。"""
    def resume(self) -> tuple[bool, int]: ...
    def resize(self, cols: int, rows: int) -> tuple[bool, str]: ...   # (ok, hresult)
    def write_input(self, data: bytes) -> int: ...
    def start_reader(self) -> None: ...                # 独立线程持续 drain
    def cancel_read(self, timeout: float) -> dict: ... # CancelSynchronousIo（唯一可靠取消）
    def output_bytes(self) -> bytes: ...
    def identity(self) -> tuple[int, int] | None: ...  # (pid, raw FILETIME)
    def is_alive(self) -> bool: ...                    # 保留 handle + GetExitCodeProcess
    def wait_exit(self, timeout: float) -> int | None: ...
    def close(self, *, close_pty=True, drain_timeout=5.0) -> dict: ...

def kill_verified(pid, expected_filetime, exit_code) -> dict:
    """单句柄原子核验后终止：OpenProcess 一次，
    FILETIME 校验 + STILL_ACTIVE 校验 + TerminateProcess 全在同一 handle。
    身份不符则不终止。这是本 spike 唯一的按 PID 终止路径。"""
```

与计划 §5.3 门禁的对应关系：

| 门禁 | 本 spike 的落点 |
|---|---|
| `atomic_with_spawn` | `spawn(resume=False)` 期间进程不可执行；assign 后 `child_in_guard_job=True` |
| `assigned` | `GuardJob.active_processes() >= 1` |
| `identity` | `SpawnRecord.pid` + `creation_filetime`（保留 handle 读取） |
| `handle_bound_for_cleanup` | `GuardJob.close()`（或 holder 死亡）→ 整树消失，`survivors=[]` |

后端接入时建议把"四要素任一不满足即拒绝 `running`"做成显式返回值，而不是靠调用方自觉。

---

## 6. 与计划 §5.3 的差异说明

- 计划 §5.3 举了 `takeover_job.py` 的 suspend/assign/resume 作为参照。本轮**没有**用它作为
  可行性依据（用户明确要求不类推）；全部结论来自对 ConPTY 序列的直接实测。
  结论方向与计划预期一致，但依据独立。
- 计划 §5.3 提到失败时"不发布 running、不开控制端点"。本 spike 的对应物是：
  失败路径不返回 session、不创建任何监听（`s3` 中 guard 成员为 0、子进程已回收）。
  控制端点/注册表属于 backend 阶段，本轮未实现。
- 计划 §5.3 的兜底方案 2（拒绝 `running` 的合法降级）**本轮不需要启用**。

---

## 7. 未验证项与残余风险

1. **ambient Job 的约束不在 Pan 控制内**。本机 ambient Job 禁止 breakaway（§3 s6），
   且我们只能观测到"成员资格"、读不到其 limit 标志（未命名 Job 无法打开）。
   若该 Job 自身带 `KILL_ON_JOB_CLOSE`，其持有者结束时我们的终端树会被一并终止——
   **这是环境事实，产品需知晓**，本轮无法从本进程内消除。
2. **长时运行与背压**：本轮最长观测约 30s（s5），未做长时间稳定性、输出洪泛、
   慢消费者背压测试。
3. **`ClosePseudoConsole` 的死锁边界**：官方对 26100+ 说明为"立即返回"，
   本机实测 `close_seconds=0.0`；但**未验证**在"输出管道已满且无人读"的对抗条件下是否仍安全，
   也未验证 26100 以下版本的行为（本机不可得）。
4. **`PSEUDOCONSOLE_INHERIT_CURSOR`**：未使用、未测（官方提示需异步应答否则可能 hang）。
5. **输入方向**：只做了 `WriteFile` 到 PTY 的基本通路，未测按键序列、Ctrl-C 注入、
   鼠标与 VT 输入编码（`\x1b[?9001h` 表明开了 win32-input-mode，但未逐项验证）。
6. **多客户端/attach**：不属于本 spike。
7. **POSIX 边界**、**命名管道/ACL 安全模型**：未涉及，属 backend 阶段。
8. **只覆盖本机单一环境**：ambient Job 的有无、Windows build、CPU 架构都可能改变
   breakaway 与 `ClosePseudoConsole` 的行为；换机需复跑 s6 与 s5。
9. **`spawn_plain_probe`（s6 用）不是生产 API**：它只用于环境表征，实现较粗糙
   （创建后立即终止），不要直接搬进 backend。

---

## 8. 清理核验

| 项目 | 结果 |
|---|---|
| 场景临时根 | 7/7 场景 `temp_removed=true`；收尾复查 `%TEMP%` 下 `conpty-spawn-*` 残留 **0** |
| 自建子进程 / 孙进程 | 每个场景结尾核对 `survivors=[]`；s4 由内核 kill-on-close 收树 |
| 终止手段 | 只有 `kill_verified`（同一 handle 内 FILETIME 精确比对后才 `TerminateProcess`）与 guard 句柄关闭；**无 `taskkill`、无 WMI/CIM 终止、无按 PID 单值杀** |
| 既有 app-server daemon（PID 32480） | 收尾核对 PID 与创建时间 `2026-10-02 20:47:42` **未变** |
| 其它既有进程 | 全程未连接、未操作、未发信号 |
| 网络 / 凭据 | 未使用模型、未读用户凭据、未连接既有 daemon |

---

## 9. 复现方法

无需第三方依赖：

```bash
cd D:/project/pan-worktrees/terminal-codex-explore-20261003

# 全场景（约 40s，52 断言）
E:/software/miniforge/python.exe audit/terminal/codex/spawn/spawn_driver.py --scenario all

# 单场景（保留临时目录便于排查）
E:/software/miniforge/python.exe audit/terminal/codex/spawn/spawn_driver.py \
    --scenario s1_atomic_suspended --keep

# 可用场景：s1_atomic_suspended s2_resize s3_assign_failure
#           s4_holder_hardkill s5_reader_cancel s6_ambient_breakaway s7_ctypes_traps
```

证据写入 `audit/terminal/codex/spawn/evidence/<scenario>-<时间戳>.json` 与 `summary.json`，
每条断言附 `detail`。驱动以退出码表示整体通过与否。

---

## 10. 文件与提交

| 文件 | 说明 |
|---|---|
| `audit/terminal/codex/spawn/spawn_win.py` | ctypes 原子 spawn 实现 + 接口草案（含 3 处实测陷阱的说明） |
| `audit/terminal/codex/spawn/child_probe.py` | 测试子进程（启动标记 / UTF-8 / 控制台尺寸 / 尾标记 / 孙进程） |
| `audit/terminal/codex/spawn/holder.py` | runner/guard-holder 替身（布局 B；供硬杀场景） |
| `audit/terminal/codex/spawn/spawn_driver.py` | 7 场景驱动 + 断言 + 证据生成 |
| `audit/terminal/codex/spawn/evidence/` | 7 份场景证据 + `summary.json` |
| `docs/design/PAN_TERMINAL_CONPTY_ATOMIC_SPAWN_20261003.md` | 本报告 |

完整 commit hash 见回执。

---

## 11. 官方参考

- Creating a Pseudoconsole session — <https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session>
- CreatePseudoConsole — <https://learn.microsoft.com/en-us/windows/console/createpseudoconsole>
- ClosePseudoConsole — <https://learn.microsoft.com/en-us/windows/console/closepseudoconsole>
- AssignProcessToJobObject — <https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject>
- Job Objects — <https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects>
- ResumeThread — <https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread>
