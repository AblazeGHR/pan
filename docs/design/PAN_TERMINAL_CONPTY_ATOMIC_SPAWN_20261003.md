# Pan Terminal：生产 ConPTY 原子 spawn 可行性（Windows ctypes spike）

- 任务：`T-TERMINAL-PTY-20261003/windows-spawn/spike/1`
- 工作树：`D:/project/pan-worktrees/terminal-codex-explore-20261003`，
  分支 `explore/terminal-codex-20261003`
- 允许路径（本轮全部产出均在其中）：`audit/terminal/codex/spawn/` 与本文档
- 本文档为 **r2（有界返工后）**：修正 liveness 原语、fail-closed 门禁、异常清理与若干措辞边界。
  返工前的证据 JSON 全部保留（`s1..s7` 旧时间戳文件 + `summary-pre-rework.json`）。
- 证据：`audit/terminal/codex/spawn/evidence/`（11 个场景 + `summary.json`），
  本轮 **118/118 断言通过**。

> 边界遵守：未修改任何生产模块；未合并/推送；未触碰既有服务、Session、Worker、
> CLI thread、8768 或既有 app-server daemon；未使用模型或用户凭据；
> 所有进程/端口/临时目录均为本 spike 自建并在 `finally` 中清理。
> 不含任何"按 PID 直接杀"的路径（无 `taskkill`、无 WMI/CIM 终止）。

---

## 0. 结论速览（实测 / 推断 / 未验证 分级）

### 0.1 原子 spawn 本体

| 门禁要素 / 问题 | 结论 | 分级 | 证据 |
|---|---|---|---|
| `atomic_with_spawn`：子进程在**从未执行过**的状态下被 assign 进 guard Job | 成立。挂起期 2.5s 内子进程的启动标记文件始终不存在；assign 后 `IsProcessInJob(child, guardHandle)=True` | 实测 | s1 |
| `assigned`：guard Job 内确有成员 | 成立。`QueryInformationJobObject` 报 `active_processes=1` | 实测 | s1 |
| `identity`：PID + 原始 FILETIME | 成立。FILETIME 为**原始 64 位**值（100ns 计数），由**保留的进程 handle** 读取，不经 PID 重开 | 实测 | s1、s8 |
| `handle_bound_for_cleanup`：句柄绑定清理 | 成立。关闭最后一个 guard 句柄后整树消失，`survivors=[]` | 实测 | s1、s4 |
| 恢复执行后真实运行、UTF-8 尾输出与退出码 | 成立。CJK + 符号经 ConPTY 原样回传；`TAIL_MARKER` 到达；退出码 7 保持 | 实测 | s1 |
| `ResizePseudoConsole` 到达被托管应用 | 成立。子进程自报 `CONSOLE_SIZE 100x30` → `132x43` | 实测 | s2 |
| assign 失败时 fail-closed | 成立。**不 resume**、guard 无成员、只清理自建子进程、标记文件始终不存在 | 实测 | s3、s9 |
| runner/holder 被硬杀后整树消失 | 成立。子进程与孙进程都消失，且 holder **没有机会运行任何清理代码** | 实测 | s4 |
| 跨平台 / POSIX | 未验证（本轮只做 Windows） | 未验证 | — |

### 0.2 r2 新增：liveness、门禁与清理

| 问题 | 结论 | 分级 | 证据 |
|---|---|---|---|
| liveness 是否可靠 | **统一到 `WaitForSingleObject(handle, 0)`**：`WAIT_OBJECT_0`=dead、`WAIT_TIMEOUT`=alive、其它=unknown/拒绝。`GetExitCodeProcess` 仅作信息字段 | 实测 | s7、s8 |
| 真实退出码 259 且保留 handle | 读作 **dead**（`is_alive=False`），且 verified-kill 拒绝执行（`reason="not_running"`） | 实测 | s8 |
| 期望 FILETIME 差 1 | verified-kill **拒绝**（`reason="identity_mismatch"`），目标保持存活；精确相等才终止 | 实测 | s8 |
| guard 归属查询失败 / 报 false | **fail-closed 拒绝 resume**（此前会静默 fall through 到 resume） | 实测（注入为包装层模拟） | s9 |
| `ResumeThread` 返回值 | 只有**先前的挂起计数恰为 1** 才算 resumed；`0xFFFFFFFF`、`0`、`>1` 一律拒绝并清理 | 实测（注入为包装层模拟） | s9 |
| 被拒绝路径是否释放伪控制台与属性表 | 成立。assign 失败路径现在会 `ClosePseudoConsole` 与 `DeleteProcThreadAttributeList`（此前两者都会泄漏） | 实测 | s9、s10 |
| `close()` 在 reader 未收敛时是否诚实 | 成立。返回 `closed=false, retryable=true` 并**保留所有权**（不置 `_closed`、不释放 handle）；同一 owner 重试可成功 | 实测（不可取消性为包装层模拟） | s10 |
| `STARTF_USESTDHANDLES` + NULL 的作用 | 本机 4 组矩阵中该标志**单独决定** stdout 是否落到 pty；`CONOUT$` 在所有组合都到达 pty | 实测（限本机/build） | s11 |

### 0.3 detach：独立的、尚未验证的产品门

**原子 spawn 成立不等于 detach 宿主可成立。** 本轮只证明了"默认整树所有权"：

- 本机宿主进程处于 ambient Job 中，且**该 Job 不允许 breakaway**
  （`CREATE_BREAKAWAY_FROM_JOB` → `ERROR_ACCESS_DENIED=5`）；我们自建的 guard Job
  以嵌套方式 assign 成功，因此默认整树清理可用。
- 但**没有任何实测表明 runner 能脱离上层 ambient Job 的寿命**。若该上层 Job
  带 `KILL_ON_JOB_CLOSE`，其持有者结束时终端树会被一并终止——这属于
  **detach 产品门**，必须在生产宿主环境下单独验证（见 §7）。

因此本文的可用结论只有一句：**原子 spawn 成立**；detach 宿主启动**待生产环境验证**。

---

## 1. 官方原文依据

本轮先读 Microsoft 主源，不据 `takeover_job.py` 之类的同类实现外推 ConPTY 行为。

**Creating a Pseudoconsole session**
<https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session>
- 先建两条**同步**通信管道，再 `CreatePseudoConsole`，再建进程。
- "Upon completion of the **CreateProcess** call … the handles given during creation
  should be freed from this process. This will decrease the reference count on the
  underlying device object and allow I/O operations to properly detect a broken channel
  when the pseudoconsole session closes its copy of the handles."
- 必须设 `EXTENDED_STARTUPINFO_PRESENT`；属性用
  `UpdateProcThreadAttribute(..., PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, hpc, sizeof(hpc), NULL, NULL)`。
- "To prevent race conditions and deadlocks, we highly recommend that each of the
  communication channels is serviced on a separate thread…"
- 托管进程若拿到无效伪控制台句柄、或会话在它启动时被关闭，客户端会弹 `0xc0000142`。
- `ClosePseudoConsole` 会终止附着其上的客户端；若原始子进程是 shell 型，树上相关进程也会被终止。

**CreatePseudoConsole**
<https://learn.microsoft.com/en-us/windows/console/createpseudoconsole>
- 返回 HRESULT；"The handle created by this function must be closed with
  **ClosePseudoConsole** when operations are complete."
- 输入/输出是 UTF-8 纯文本夹带 VT 序列。

**ClosePseudoConsole**
<https://learn.microsoft.com/en-us/windows/console/closepseudoconsole>
- 关闭会给仍连着的客户端发 `CTRL_CLOSE_EVENT`；应"先关闭输出管道，或继续读直到
  `ClosePseudoConsole` 返回"。
- **"Starting Windows 11 24H2 (build 26100) ClosePseudoConsole will return immediately
  to avoid accidental deadlocks. Earlier versions will wait indefinitely…"**
  → 本机 build 26200 属"立即返回"分支（实测 `seconds≈0.0`）。

**AssignProcessToJobObject**
<https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject>
- `hProcess` 需 `PROCESS_SET_QUOTA | PROCESS_TERMINATE`。
- "If the process is already associated with a job, the job specified by *hJob* must be
  empty or it must be in the hierarchy of nested jobs to which the process already belongs,
  and it cannot have UI limits set."
- `JOB_OBJECT_SECURITY_ONLY_TOKEN` 场景要求进程以 `CREATE_SUSPENDED` 创建。
- "By default, all child processes are associated with the immediate job and every job in
  the parent job chain."

**Job Objects**
<https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects>
- "After a process is associated with a job, the association cannot be broken."
- "…if the job has the JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE flag specified, closing the
  last job object handle terminates all associated processes and then destroys the job
  object itself."
- 子进程默认加入 Job；仅当设了 `JOB_OBJECT_LIMIT_BREAKAWAY_OK`（且子进程用
  `CREATE_BREAKAWAY_FROM_JOB`）或 `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK` 时才不加入。
- 若两个 breakaway 极限都不设，"if a child process attempts to associate itself or
  another child process with a job by calling AssignProcessToJobObject, the call will fail."

**ResumeThread**
<https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread>
- 递减挂起计数；成功返回**先前的挂起计数**，失败返回 `(DWORD)-1`。
  "If the return value is 1, the specified thread was suspended but was restarted."

**GetProcessTimes**
<https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes>
- 四个 `[out] FILETIME` 参数**均未标记 optional**。给它们传 `NULL` 违反该 API 的指针契约；
  本机由此观测到的访问违例是**违反契约的后果**，不是"NULL 合法但不支持"，也不是 ctypes 特有缺陷。

---

## 2. 实测环境（可复现）

| 项 | 值 |
|---|---|
| OS | Windows 11，build **26200**.9457（DisplayVersion 25H2） |
| Python | 3.12.12（conda-forge, MSC v.1944, AMD64）— `E:/software/miniforge/python.exe` |
| 依赖 | **无第三方依赖**：只用 `ctypes` + 标准库 |
| ConPTY API | `CreatePseudoConsole` / `ResizePseudoConsole` / `ClosePseudoConsole` 均可用 |
| 每次运行的临时根 | `%TEMP%/conpty-spawn-<scenario>-<rand>/`，场景结束删除 |
| 提交的子进程 | 只有本 spike 自建的 `child_probe.py` / `holder.py` |
| 网络 | 不使用；不连接任何既有 daemon 或服务 |

---

## 3. 实测结果

### s1 `s1_atomic_suspended`（15/15）

| 观测 | 值 |
|---|---|
| 子进程 | pid `46020`，raw FILETIME `134354370138654871`（由保留的 `hProcess` 读取） |
| assign | `child_in_guard_job=True`，`assign_last_error=0`；guard `active_processes=1` |
| 挂起期间（2.5s 窗口） | 启动标记文件**不存在**；进程存活但未执行 |
| `ResumeThread` | 返回 `1`（"was suspended but was restarted"） |
| 恢复后 | 标记文件出现；ConPTY 收到 `UNICODE 中文测试 ✓ αβγ — utf8-roundtrip` 与 `TAIL_MARKER` |
| 退出码 | `7` |
| 关闭 guard 句柄后 | `survivors=[]` |
| 各阶段耗时 | 建管道 0.03 ms、`CreatePseudoConsole` 7.84 ms、`CreateProcessW` 3.07 ms、`assign` **0.03 ms** |

`assign` 仅 0.03 ms —— 从进程创建到入组之间子进程始终挂起，窗口内不可能产生逃逸后代。

### s2 `s2_resize`（4/4）
`ResizePseudoConsole(hpc, {132,43})` 返回 `S_OK`；子进程在自身运行期间重新查询并打印
`CONSOLE_SIZE 132x43`（初始 `100x30`）——resize 确实到达**被托管应用**。

### s3 `s3_assign_failure`（7/7）
注入 assign 失败后：`SpawnDenied(stage="assign_guard_job")`、`resumed=false`、
2s 后标记文件仍不存在、guard `active_processes=0`、被拒子进程已回收。

### s4 `s4_holder_hardkill`（7/7）
holder 复刻计划 §5.1"布局 B"（出生即拥有 PTY、自持 kill-on-close guard、被硬杀前不清理）。

| 观测 | 值 |
|---|---|
| holder / 子进程 / 孙进程 | pid 34188；guard 成员数（ready）2 |
| 硬杀方式 | `kill_verified`：同一 handle 内 FILETIME 完全相等 → `TerminateProcess` |
| 硬杀 3s 后 | `survivors=[]` —— 子进程与孙进程**全部消失** |

### s5 `s5_reader_cancel`（9/9）
对比两条收敛路径：

| 路径 | 收敛 | 错误码 | 耗时 |
|---|---|---|---|
| **A：`CancelSynchronousIo(readerThread)`** | 收敛 | `995` (`ERROR_OPERATION_ABORTED`) | **0.0 s** |
| **B：关闭输出读端 handle** | 最终收敛 | `6` (`ERROR_INVALID_HANDLE`) | **6.086 s**（子进程自行退出、写端消失之后） |

**在本轮实测中** `CancelSynchronousIo` 是可靠且迅速的取消手段；路径 B 的收敛发生在
写端消失之后，说明"关 handle"**不能**作为即时的取消手段。这是**本次实测范围内的结论**，
不代表它是唯一可能的实现（其它同步/异步 I/O 设计不在本轮范围内）。

### s6 `s6_ambient_breakaway`（6/6，本机边界如实记录）

| 观测 | 值 |
|---|---|
| 宿主是否在 ambient Job | **是** |
| 普通子进程（无 breakaway 标志） | 创建成功，`in_any_job=true` |
| **`CREATE_BREAKAWAY_FROM_JOB` 子进程** | **创建失败**：`last_error=5` / `ERROR_ACCESS_DENIED` |
| 自建 guard Job 的嵌套 assign | **成功**：`assign_last_error=0`，`child_in_guard_job=true`，且 `child_in_ambient_job=true` |

含义：**本机 ambient Job 不允许 breakaway**，但我们自己的嵌套 guard 仍然生效，
默认整树清理因此可用。这**不**证明 runner 能脱离上层 Job 的寿命（§0.3、§7）。

### s7 `s7_api_contract`（6/6）

- `GetProcessTimes` 四个 `[out]` 参数**未标记 optional**；传 `NULL` 违反指针契约，
  本机表现为 `OSError('exception: access violation writing 0x0000000000000000')`。
  契约合规调用（四个真实 `FILETIME` 指针）成功，返回原始 64 位值。
- 结构体尺寸自检符合 Win32 ABI：`STARTUPINFOW=104`、`STARTUPINFOEXW=112`、
  `lpAttributeList` 偏移 `104`、`PROCESS_INFORMATION=24`、
  `JOBOBJECT_EXTENDED_LIMIT_INFORMATION=144`。
- `wait_state` 语义实测：运行中的子进程 `alive`，`TerminateProcess` 后 `dead`。

### s8 `s8_liveness_primitives`（12/12）

| 观测 | 值 |
|---|---|
| 子进程真实退出码 259（= `STILL_ACTIVE` 数值），**保留 handle** | `wait_exit` 返回 `259`；`exit_code()` 信息字段也读 `259` |
| 同 handle 的 liveness | **`dead`**；`is_alive() == False` |
| verified-kill（已退出、exit code 259） | **拒绝**：`killed=false`、`reason="not_running"`、`state="dead"` |
| verified-kill（活进程但 FILETIME 差 1） | **拒绝**：`reason="identity_mismatch"`；随后目标仍 `alive` |
| verified-kill（FILETIME 精确相等） | 成功（正对照） |

这一组正是"仅看退出码会误判"的负例：若 liveness 由 `GetExitCodeProcess != 259` 决定，
上面这个已退出进程会被报告为存活，并有被误终止的风险。

### s9 `s9_failclosed_gates`（35/35）

5 个注入用例，全部按预期落在对应门禁：

| 用例 | 拒绝阶段 | resumed | guard 成员（仍持有 handle 时） |
|---|---|---|---|
| guard 归属查询失败 | `verify_guard_membership` | false | 0 |
| guard 归属报 false | `verify_guard_membership` | false | 0 |
| `ResumeThread` 返回 0 | `resume_thread` | false | 0 |
| `ResumeThread` 返回 2 | `resume_thread` | false | 0 |
| `ResumeThread` 返回 `0xFFFFFFFF` | `resume_thread` | false | 0 |

每个用例另外断言：子进程**从未执行**（标记文件不存在）、guard 在仍持有 handle 时成员为 0、
handle 关闭后查询返回 `None`（不返回编造的数字）、被拒路径已释放伪控制台与属性表。

**重要限定**：这些失败是**包装层注入**（`spawn()` 的参数），**不是真实 OS 异常的复现**。
它们证明的是**门禁逻辑会拒绝**，不构成对真实系统错误的证据。

### s10 `s10_resource_release`（14/14）

- **被拒路径的资源释放**：`pty_closed=true`、`attr_list_deleted=true`、
  管柄与进程 handle 均已关闭（此前 assign 失败会泄漏 HPCON、属性表从不释放）。
- **`close()` 的诚实性**：把 reader 线程 handle 与输出 handle 对 `close()` 隐藏
  （包装层模拟"既不能取消也不能强制收敛"）后，`close()` 返回
  `closed=false, retryable=true, errors=["reader_not_converged"]`，
  **`_closed` 仍为 False、HPCON 仍在**；恢复后同一 owner 重试 → `closed=true`，
  并确实释放了伪控制台与属性表。

### s11 `s11_std_handle_matrix`（3/3）

| `STARTF_USESTDHANDLES`+NULL | `bInheritHandles` | `CONOUT$` 到 pty | stdout 到 pty |
|---|---|---|---|
| 是 | False | 是 | **是** |
| 是 | True | 是 | **是** |
| 否 | False | 是 | **否** |
| 否 | True | 是 | **否** |

在本机这一次矩阵里，`STARTF_USESTDHANDLES` 单独决定了 stdout 的去向，`bInheritHandles`
的取值没有改变结果。**范围限定**：这是本机、本 build、本父进程拓扑下的一次 4 组矩阵，
不是"所有环境都如此"的普适结论。

---

## 4. 构建过程中发现并修正的问题（保留过程记录）

1. **伪控制台建好 ≠ 子进程 std 句柄走 ConPTY（最隐蔽）**
   只设 `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` 时，子进程的**控制台**确实是伪控制台
   （写 `CONOUT$` 能到达 pty），但 stdout/stderr 仍指向父进程句柄。症状是
   "pty 只吐 16 字节初始化序列、子进程输出跑到宿主控制台"。s11 矩阵显示
   `STARTF_USESTDHANDLES` + NULL 可修复；`spawn_win.py` 已固定该设置。
   （范围见 §3 s11，不外推到所有环境。）

2. **`CreatePseudoConsole` 用过的两个 handle 必须在 `CreateProcess` 后由本进程释放**
   不释放会额外持有底层设备对象引用，导致输出管道读不到 EOF / 断链。

3. **关闭读端 handle 不能作为即时取消**（§3 s5）。收尾先 `CancelSynchronousIo`，且有界。

4. **r2：liveness 必须走 `WaitForSingleObject(0)`**。`GetExitCodeProcess` 会把
   "真实退出码 259"与"仍在运行"混为一谈（§3 s8 负例）。

5. **r2：`QueryInformationJobObject` 在 handle 已关闭时不会可靠失败** ——
   实测给已关闭的 Job 查询返回了成功和一个陈旧数字（20）。因此
   `active_processes()` 在自持 handle 为空时直接返回 `None`，不编造数字。

6. **r2：门禁漏洞**。此前"guard 归属查询失败"或"resume 计数异常"都会继续走 resume；
   现在两者都 fail-closed，并且被拒路径释放伪控制台与属性表（此前会泄漏）。

7. **r2：`close()` 不诚实**。此前 reader 未收敛仍释放 owner 并置 `_closed=True`；
   现在返回 `retryable=true` 并保留所有权，重试可成功。
   同时 `cancel_read` 先置 stop 标志，避免取消恰好落在两次 `ReadFile` 之间的空隙后
   reader 重新阻塞。

8. **过程事实**：早期一版调试脚本用**无超时**的 `ReadFile` 排空管道，直接挂死（人工终止）。
   现版本所有阻塞等待均有显式上界。

---

## 5. 对外接口草案（供后续 backend 使用，本轮不实现 backend）

```python
# ---- 统一的 liveness 原语 -------------------------------------------------
def wait_state(hprocess) -> str:
    """WaitForSingleObject(h, 0)：'dead' | 'alive' | 'unknown'。
    WAIT_OBJECT_0=dead；WAIT_TIMEOUT=alive；其它（含 WAIT_FAILED）=unknown，拒绝猜测。"""

def read_exit_code_informational(hprocess) -> int | None: ...   # 仅信息，259 有歧义

class GuardJob:
    """JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 树守卫；最后一个句柄关闭即整树终止。"""
    def __init__(self, name: str | None = None) -> None: ...
    raw_handle: int
    def assign(self, hprocess: int) -> tuple[bool, int]: ...
    def active_processes(self) -> int | None:   # 自持 handle 为空 -> None，不编造数字
        ...
    def terminate(self, exit_code: int = 1) -> bool: ...
    def close(self) -> None: ...

class SpawnRecord:            # 计划 §5.4 的 TerminalRecord 从这里取值
    pid: int
    creation_filetime: int     # 原始 64 位 100ns，精确比较，非容差
    ...

class ConPtySession:
    @classmethod
    def spawn(cls, command_line, cwd, cols, rows, guard, *,
              resume=True, inject_*=...) -> "ConPtySession":
        """原子：挂起创建 → 入 guard → 校验归属 → resume（且校验恰好发生一次）。
        任一环节不可证即 SpawnDenied，且只清理本次自建资源。"""
    def liveness(self) -> str: ...            # 'dead' | 'alive' | 'unknown'
    def is_alive(self) -> bool: ...           # 仅 liveness()=='alive'
    def exit_code(self) -> int | None: ...    # 信息字段
    def resize(self, cols, rows) -> tuple[bool, str]: ...
    def write_input(self, data: bytes) -> int: ...
    def start_reader(self) -> None: ...
    def cancel_read(self, timeout: float) -> dict: ...   # 先置 stop 再取消，重试至收敛或超时
    def wait_exit(self, timeout: float) -> int | None: ...
    def close(self, *, close_pty=True, drain_timeout=5.0) -> dict:
        """closed=True 仅当每步都成功；否则 closed=False/retryable=True 并保留所有权。"""
    def _full_local_cleanup(self) -> dict: ...  # 伪控制台 + 属性表 + 管柄 + 进程 handle

def kill_verified(pid: int, expected_filetime: int, exit_code: int = 0xDEAD) -> dict:
    """单句柄原子核验后终止：
    OpenProcess(SYNCHRONIZE|QUERY_LIMITED_INFORMATION|TERMINATE) 一次；
    liveness(state) 校验 + FILETIME 精确校验 + TerminateProcess 全在同一 handle。
    state 为 unknown/dead 或 FILETIME 不符时一律拒绝并说明原因。
    本 spike 唯一的按 PID 终止路径。"""
```

与计划 §5.3 门禁的对应关系：

| 门禁 | 本 spike 的落点 |
|---|---|
| `atomic_with_spawn` | `spawn(resume=False)` 期间进程不可执行；assign 后 `child_in_guard_job=True` |
| `assigned` | `GuardJob.active_processes() >= 1`，且归属查询失败/为假即拒绝 |
| `identity` | `SpawnRecord.pid` + 原始 64 位 `creation_filetime`（保留 handle 读取） |
| `handle_bound_for_cleanup` | `GuardJob.close()`（或 holder 死亡）→ 整树消失，`survivors=[]` |

后端接入时建议把"四要素任一不可证即拒绝 `running`"做成显式返回值。

---

## 6. 与计划 §5.3 的差异说明

- 计划 §5.3 举 `takeover_job.py` 的 suspend/assign/resume 作参照。本轮**未**用它作为
  可行性依据；全部结论来自对 ConPTY 序列的直接实测。
- 计划 §5.3 的失败行为（不发布 running、不开控制端点）：本 spike 的对应物是失败路径
  不返回 session、guard 无成员、只回收自建子进程。控制端点/注册表属 backend 阶段，未实现。
- 计划 §5.3 的兜底方案 2（拒绝 `running` 的合法降级）**本轮不需要启用**。

---

## 7. 未验证项与残余风险

**detach 相关（产品门，必须单独验证）**

1. **runner 能否脱离上层 ambient Job 的寿命，本机未验证**。我们只证明了"嵌套 guard
   在默认路径下有效"；ambient Job 的 limit 标志读不到（未命名 Job 无法打开）。
   若该 Job 带 `KILL_ON_JOB_CLOSE`，其持有者结束时终端树会被一并终止。
   → **detach 宿主启动必须在生产环境单独验证**，不能用本报告覆盖。

**其它**

2. **长时运行与背压**：单场景最长观测约 30s（s5），未做长时间稳定性、输出洪泛、慢消费者背压。
3. **`ClosePseudoConsole` 的旧系统行为**：官方称 26100+ 立即返回；本机实测 `≈0.0s`。
   旧 build 的"等待客户端"行为**未测**。本实现按官方建议先关输出管道再关伪控制台，
   以降低满管道风险，但**这是设计选择而非实测结论**。
4. **`PSEUDOCONSOLE_INHERIT_CURSOR`**：未使用、未测（官方提示需异步应答否则可能 hang）。
5. **输入方向**：只做了基本 `WriteFile` 通路，未测按键序列、Ctrl-C 注入、鼠标与 VT 输入编码。
6. **s9/s10 中的失败均为包装层注入**，不是真实 OS 异常；真实异常路径未覆盖。
7. **s11 矩阵只覆盖本机、本 build、本父进程拓扑**（4 组），未做跨 build/跨拓扑验证。
8. **多客户端/attach**、**POSIX 边界**、**命名管道/ACL 安全模型**：不在本 spike 范围。
9. **`spawn_plain_probe`（s6 用）不是生产 API**：仅用于环境表征，实现较粗糙。

---

## 8. 清理核验

| 项目 | 结果 |
|---|---|
| 场景临时根 | 11/11 场景 `temp_removed=true`；收尾复查 `%TEMP%` 下 `conpty-spawn-*` 残留 **0** |
| 自建子进程 / 孙进程 | 每场景结尾核对 `survivors=[]`；s4 由内核 kill-on-close 收树 |
| 终止手段 | 只有 `kill_verified`（同一 handle 内 FILETIME 精确比对后才 `TerminateProcess`）与 guard 句柄关闭；**无 `taskkill`、无 WMI/CIM 终止、无按 PID 单值杀** |
| 既有 app-server daemon（PID 32480） | 收尾核对 PID 与创建时间 `2026-10-02 20:47:42` **未变** |
| 其它既有进程 | 全程未连接、未操作、未发信号 |
| 网络 / 凭据 | 未使用模型、未读用户凭据、未连接既有 daemon |
| 旧证据 | 返工前的 `s1..s7` JSON 与 `summary-pre-rework.json` **保留未删** |

---

## 9. 复现方法

无需第三方依赖：

```bash
cd D:/project/pan-worktrees/terminal-codex-explore-20261003

# 全场景（约 55s，118 断言）
E:/software/miniforge/python.exe audit/terminal/codex/spawn/spawn_driver.py --scenario all

# 单场景（保留临时目录便于排查）
E:/software/miniforge/python.exe audit/terminal/codex/spawn/spawn_driver.py \
    --scenario s9_failclosed_gates --keep
```

可用场景：`s1_atomic_suspended`、`s2_resize`、`s3_assign_failure`、`s4_holder_hardkill`、
`s5_reader_cancel`、`s6_ambient_breakaway`、`s7_api_contract`、`s8_liveness_primitives`、
`s9_failclosed_gates`、`s10_resource_release`、`s11_std_handle_matrix`。

证据写入 `audit/terminal/codex/spawn/evidence/<scenario>-<时间戳>.json`，每条断言附 `detail`；
驱动以退出码表示整体通过与否。

---

## 10. 文件与提交

| 文件 | 说明 |
|---|---|
| `audit/terminal/codex/spawn/spawn_win.py` | ctypes 原子 spawn 实现 + 接口草案（含 §4 各问题的说明） |
| `audit/terminal/codex/spawn/child_probe.py` | 测试子进程（启动标记 / UTF-8 / 控制台尺寸 / 尾标记 / 孙进程） |
| `audit/terminal/codex/spawn/holder.py` | runner/guard-holder 替身（布局 B；供硬杀场景） |
| `audit/terminal/codex/spawn/spawn_driver.py` | 11 场景驱动 + 断言 + 证据生成 |
| `audit/terminal/codex/spawn/evidence/` | 场景证据 + `summary.json`（并保留返工前 JSON） |
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
- GetProcessTimes — <https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes>
