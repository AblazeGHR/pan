# `packages/core/platform/` — 平台层

跨平台差异只允许存在于本目录（及各平台 backend）。业务代码通过接口使用，
不在模块里散布 `if os.name` / `sys.platform`。见根 `AGENTS.md`「跨平台」与
`docs/plans&overviews/CROSS_PLATFORM_PLAN_20261025.md`。

本目录当前包含：

| 文件 | 作用 |
|---|---|
| `process.py` | `ProcessOwner` 契约 + Windows 实现（包装 `terminal.guard` / `terminal.identity`） |
| `process_posix.py` | POSIX 实现（`start_new_session` + `killpg` + 持久身份） |
| `runtime_lock.py` | `job.runtimeOwner` 进程存活互斥；Windows 懒加载 msvcrt，Linux/macOS 懒加载 flock；锁文件不删除 |
| `README.md` | 本文件 |

> `__init__.py` 由 A 流（平台骨架 / capability registry）负责，本流不修改。
> 在它合入前，本目录按 PEP 420 命名空间包工作：`from packages.core.platform.process
> import ProcessOwner` 可直接使用。**不要**在本目录外 `import platform` 期望拿到
> 标准库——包内相对导入请用 `packages.core.platform.<module>`。

---

## 1. 契约

`ProcessOwner`（`process.py`）是“谁拥有进程树、谁负责杀、谁证明退出”的唯一接口。
两份实现同义；差异只在平台能力（见 §2）。

| 方法 | 语义 |
|---|---|
| `spawn(argv, *, cwd, env, stdin, stdout, stderr, hide_window)` | 启动子进程并绑定所有权；返回 `OwnedProcess`（含 `pid` + `identity`）。无命令 / 无法绑定身份一律拒绝，不交出半可用句柄。 |
| `identity(handle \| pid)` | 已绑定身份；传 PID 时现查（`None` = 查不到，**不等于已死**）。 |
| `is_alive(handle \| identity \| pid)` | 三态：`ALIVE` / `DEAD` / `UNKNOWN`。不做“是否仍是我们的进程”的判断。 |
| `check(identity)` | 严格复核：`status` + `ours`。只有 `verified_alive`（`ALIVE` 且 `ours=True`）才允许终止。 |
| `wait(handle, timeout=...)` | 等根进程退出并返回退出码；超时只报 `timed_out`，**不杀进程**。 |
| `terminate(handle, grace=5.0)` | 优雅停止整棵自有树：能发信号就 `SIGTERM`→宽限→`SIGKILL`；平台没有优雅信号时核验后硬终止。返回 `remaining`（空 = 已清空）。 |
| `kill_tree(handle, timeout=5.0)` | 立即强杀整棵树并在 `timeout` 内核对残留。 |
| `recoverable_identity(handle)` | JSON 安全的持久记录（`schema` / `platform` / `pid` / `kind` / `birth` / `boot_id` / `pgid` / `image`）。 |
| `verify(record)` / `recover(record, terminate=..., grace=..., timeout=...)` | 崩溃后按记录重新校验；**只有身份成立才终止**。`recover` 的 `action`：`none` / `already_dead` / `refused_identity` / `refused_pgid_changed` / `unknown` / `terminated`。 |

设计纪律：

1. **身份来自被拥有的进程本身**：Windows 是 spawn 时保留句柄上的 FILETIME；
   POSIX 是同一 PID 的 `stat`/`ps` 出生标记。不得事后按 PID 重开冒充。
2. **fail-closed**：查不到 / 打不开 / 身份不可比对 → `UNKNOWN`，绝不冒充 `DEAD`；
   不可验证的目标拒绝终止。
3. **查询失败抛 `OwnershipUnknownError`**；“查询成功但仍有残留”用
   `remaining` / `confirmed=False` 表达。两者都不得被调用方当成成功。
4. **句柄绑定 owner 实例**：跨 owner 使用抛 `ValueError`。

## 2. 平台差异

| 维度 | Windows（`WindowsProcessOwner`） | POSIX（`PosixProcessOwner`） |
|---|---|---|
| 所有权原语 | Job Object（`KILL_ON_JOB_CLOSE`，无 breakaway 位），整树枚举走 `QueryInformationJobObject` | 独立 session/进程组（`start_new_session=True`，`pgid == pid`），整树用 `killpg` |
| spawn 原子性 | `Popen` 后立即 `AssignProcessToJobObject` 并证明成员；**不是** `CREATE_SUSPENDED` 原子语义 | `fork` 后即 `setsid`；无 assign 窗口 |
| 身份来源 | 保留句柄的 64 位 FILETIME（100ns，字符串保存） | Linux：`/proc/<pid>/stat` 字段 22 + `boot_id`；macOS：`ps -o lstart=`（**1 秒分辨率**，`LC_ALL=C`） |
| 身份强度 | 最强（精确 FILETIME） | Linux 强（starttime → clock ticks，boot_id 防跨重启混淆）；macOS 较弱（秒级） |
| 优雅退出 | **无 SIGTERM 等价物**：`terminate` = 核验后 `TerminateProcess` + `TerminateJobObject`（与现状一致） | `SIGTERM`（进程组）→ `grace` → `SIGKILL`；返回前用 `killpg(pgid, 0)` 核对 |
| Ctrl+C | 子进程在隐藏控制台，收不到前台 Ctrl+C | 子进程在新 session，收不到前台 Ctrl+C（**这就是 Phase B 要修的行为**） |
| 崩溃兜底 | Job 句柄随宿主消失 → 内核终止整树；`recover` 多数情况只会看到 `already_dead` | 子进程会成为孤儿继续运行；`recover` 用持久身份校验后 `killpg` 回收 |
| 崩溃回收边界 | 无 Job 句柄时无法枚举残留，只能核验并终止根进程 | **leader 已退出时拒绝按 pgid 回收**（无法证明该 pgid 仍属于本树）；记录 `pgid != pid` 同样拒绝（不是本层 `setsid` 新建的组） |
| `hide_window` | 生效：`CREATE_NO_WINDOW` / `CREATE_NEW_CONSOLE` | 忽略（POSIX 无窗口语义，参数保留以便同签名） |
| `close()` | 关闭 Job 句柄：若有存活成员，内核终止整棵树（最后兜底） | 仅标记关闭（POSIX 无内核句柄） |

按平台取实现用 `default_process_owner()`（Windows → `WindowsProcessOwner`，
POSIX → `PosixProcessOwner`，其它平台抛 `UnsupportedPlatformError`）。
不支持时显式失败：

- `WindowsProcessOwner()` 在非 Windows 构造即抛 `UnsupportedPlatformError`；
- `PosixProcessOwner` 构造无副作用，平台方法调用时抛 `UnsupportedPlatformError`；
- 两个模块**都能在任何平台导入**（顶层不 import `ctypes.wintypes` / `msvcrt` /
  `winreg` / `pywin32`；Windows 的 `guard`/`identity` 在方法内延迟导入）。

## 3. 未验证项（不得当作已证明）

- **POSIX 真实系统调用未在本机验证**：本分支在 Windows 上开发，`start_new_session`
  / `killpg` / `/proc` / `ps` 的真实语义需要 ubuntu / macos CI 或实机。
  契约测试 `TestPosixProcessOwnerContract` 在 Windows 上**显式 skip**（写了原因），
  不会静默通过。
- **macOS `ps -o lstart=`**：只做了字符串解析单测；未在 macOS 实机跑过，1 秒分辨率
  意味着同一秒内的 PID 复用理论上不可区分（已按“弱于 Linux”记录）。
- **Windows spawn 非原子**：`Popen` 到 `assign` 之间有极短窗口，子进程可能已执行。
  非 PTY 的 Worker/Job 现状也是这个语义；PTY 路径必须继续用
  `terminal/spawn_win.spawn_conpty_suspended`（`CREATE_SUSPENDED` → assign → resume）。
- **POSIX 僵尸与进程组**：`killpg(pgid, 0)` 在僵尸未回收时会报告“组仍存在”；
  `terminate`/`kill_tree` 会先 `Popen.poll()` 回收根进程。非根成员的僵尸由它们
  自己的父进程负责回收。
- **未接入任何运行时**：本分支只交付库；worker / jobs / terminal 的调用路径未改。

## 4. 如何接入 Worker（下一步）

现状（审计 P1-04 / P1-05）：`packages/core/worker.py` 用
`create_subprocess_exec` + 管道，POSIX 没有 `setsid`；终止用 psutil
`children(recursive=True)`；Worker 只存在内存里，崩溃后无法回收。

建议顺序（单独 PR，不要在平台层 PR 里做）：

1. **spawn**：把 `asyncio.create_subprocess_exec(...)` 换成
   `owner.spawn(argv, cwd=..., env=..., stdin=DEVNULL, stdout=PIPE, stderr=STDOUT)`
   并用 `asyncio.to_thread` / 读线程桥接管道（本层只做所有权，不接管 I/O）。
   - POSIX 自动获得独立 session：前台 Ctrl+C 不再直打 CLI；
   - Windows 保持 `CREATE_NO_WINDOW`，并额外获得 Job 整树所有权。
2. **停止**：`worker.stop()` 改调 `owner.terminate(handle, grace=...)`；单任务中断
   （Esc / control message）继续走 stdin 协议，**不要**用信号表达。
   `_kill_pid_tree` 的 psutil 路径作为不支持的平台/异常兜底保留或删除。
3. **持久化**：spawn 成功后立即把 `owner.recoverable_identity(handle)` 写入
   Session 元数据或 `data/workers/<worker_id>.json`（含 `worker_id`、`session_id`、
   `workdir`、CLI 类型）；确认退出后删除。记录里**不要**写密钥/账号。
4. **启动回收**：Pan 启动时扫描记录：
   - `verify(record).verified_alive` → 旧 Worker 仍活着：按产品策略接管或
     `recover(record, terminate=True)`；
   - `action == "refused_identity"` → PID 已被复用：标记记录失效，**绝不**杀；
   - `action == "already_dead"` / `unknown` → 只更新状态，不假装已清理。
5. **Ctrl+C**：Pan 的 SIGINT handler 只做优雅关闭（按记录/句柄逐个
   `terminate(grace=...)`，总预算有界），不要依赖终端信号传播。
6. **收尾**：确认退出（`wait` 或 `terminate` 的 `remaining == ()`）后才 `handle.close()`；
   Windows 上提前 `close()` 会触发 `KILL_ON_JOB_CLOSE`（虽然是兜底，但会跳过优雅路径）。

## 5. 如何接入 Jobs / Terminal

- **Jobs（independent runner/service）**：把 `independent_runner._identity()`（只认
  `/proc`）与 `independent_service.recover()`（Windows-only probe）换成
  `owner.recoverable_identity` / `owner.recover`；runner 的 `killpg` 换成
  `owner.terminate`。注意 runner 是**独立进程**：它自己创建 owner，Pan 只读它写入的
  持久记录。
- **Terminal / Rewind**：PTY 的原子 spawn 继续用 `spawn_win.spawn_conpty_suspended`；
  其 `JobObjectGuard` 与 `ProcessOwner` 共用同一个 `terminal.guard` 实现，互不冲突。
  POSIX PTY backend 属于 Phase D，不在本层承诺。

## 6. 测试

```bash
# 契约测试：Windows 实跑；POSIX 用例在非 POSIX 主机显式 skip
python -m pytest tests/test_platform_process_contract.py -q

# POSIX 控制流单测（任意平台；用 seam 驱动 SIGTERM→SIGKILL / 身份拒绝 / 回收）
python -m pytest tests/test_platform_process_posix_unit.py -q
```

证据边界：Windows 上是**真实 Job Object / FILETIME 内核调用**；POSIX 在本分支只有
控制流单测，真实语义待 ubuntu/macos CI（`TestPosixProcessOwnerContract`）。
