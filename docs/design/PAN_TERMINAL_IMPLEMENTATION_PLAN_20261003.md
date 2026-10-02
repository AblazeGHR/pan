# Pan Terminal 首版实施计划（2026-10-03）

- 任务：T-TERMINAL-PTY-20261003 的实施计划（有界、可执行）。本文件是**计划**，不是实现；
  本文档提交本身不改任何生产代码。
- 计划作者工作树：`D:/project/pan-worktrees/terminal-lifecycle-explore-20261003`
  （branch `explore/terminal-lifecycle-20261003`）。
- 设计输入（全部只读）：
  1. 需求 brief：`docs/design/PAN_TERMINAL_PTY_EXPLORATION_BRIEF_20261003.md`（内容与集成树一致）；
  2. 生命周期报告：`docs/design/PAN_TERMINAL_LIFECYCLE_JOBS_20261003.md`（follow-up `9bd858e2`，已验收）；
  3. 公共契约报告（校准版）：集成树 `D:/project/pan-worktrees/pr6-terminal-20261002`
     `docs/design/PAN_TERMINAL_PTY_CONTRACT_20261003.md`（含 `eb4d5589` 首版范围校准）；
  4. 公共既有审查：`docs/PR6_TERMINAL_REVIEW_20261002.md`（62 项回滚测试的出处）；
  5. PR #6 只读源码：`D:/project/pan-worktrees/pr6-terminal-source-20261002`（HEAD `387a43ec`，
     `packages/core/rewind/driver.py` 的 `_PtySession` L477-582、driver 菜单逻辑）；
  6. 集成树状态核对：截至本计划写作时，集成树仅有 docs/audit 变更，`packages/` 无终端代码，
     `packages/core/rewind/` 尚不存在（PR6 代码未并入）。
- 已定语义（用户已决定，本计划不再索要）：首版全局 Terminal，可选关联 Workspace / Agent Session；
  浏览器显示或断开不改变 runtime；默认终端随 Pan 服务生死，显式关闭终端才终止并杀整树；
  显式 runtime detach 保留原 PTY/PID 并可重连；PR #6 回滚复用公共核心、CBC 菜单留在 driver；
  原生 Adapter 无中断 TUI 延后（Codex 实测目标 thread 的 turn 在 TUI 退出后 `interrupted`，
  CBC 未证实同 Agent 切换）；ConPTY host/IO 迁移**不必首版**。
- 生产保障红线（本计划全篇遵守）：
  - `PsutilTreeTerminator` 只是自动化/测试观察实现，**不是**整树所有权证明（契约 §7 已知失败 3）；
  - 契约原型的“声明式 gate”字段与 `JobObjectTreeTerminator(NotImplementedError)` 只是接口占位，
    **不是**生产实现；
  - `PyteScreenObserver(fidelity="partial")` 不能承担网页 TUI 状态恢复，**不是**权威快照；
  - 生命周期探针（`audit/terminal/lifecycle/`）证明的是寿命/所有权语义，**不是** race-free 生产 spawn。

---

## 1. 首版范围与不做清单

### 1.1 首版做

| 项 | 内容 |
| --- | --- |
| 全局终端 | `term_` 命名空间的独立对象；`TerminalRegistry` 持久化（`data/terminals/`）；创建/列表/查看/关闭/显式 detach；可选关联 `workspace_id` / `session_id`（仅元数据，不强制窗口归属） |
| PTY 会话 | Windows ConPTY 真实 shell（默认 `cmd.exe /q /d`，可配置 pwsh）；交互输入、Ctrl-C/D、Tab、方向键、粘贴、动态 resize |
| 多连接 | 一个终端多个观察连接、同一时刻一个输入控制权（control lease + generation + 撤销） |
| 输出通道 | runner 内**权威仿真器常驻**（runtime 生存期）+ 有界输出日志（绝对字节偏移）、gap 返回、浏览器重连/刷新用服务器快照恢复；慢客户端断开并从快照恢复；浏览器非权威 |
| 生命周期 | 默认随 Pan 服务生死（正常关闭显式 stop；服务崩溃由 lease 超时自停）；显式关闭终端=整树终止；显式 detach=durable（保留原 PTY/PID，可重连）；Pan 重启 reconcile |
| 服务与前端 | Pan REST/WS 入口 + React xterm.js 面板；MCP `terminal_*` 工具 |
| PR #6 | 回滚复用公共核心（`_PtySession` 退化为薄封装），回滚对外语义不变，62 项回滚测试保持通过 |
| 平台 | Windows 优先（实测路径）；POSIX 只保留接口与降级提示 |

### 1.2 首版不做（明确延后）

- **原生 Adapter 无中断 TUI**（CBC/Codex）：延后。锚点已留好（`AutomationDriver`、`ScreenObserver`），
  但首版不承诺任何“切换原生 TUI 且不打断运行中任务”的能力，也不改任何 Adapter 传输。
- **ConPTY host/IO 迁移**（把已运行 PTY 的主控移交另一进程）：不做也不声称；
  Job 句柄所有权移交（`DuplicateHandle`，已实测可行）仅作为未来备选记录，不进入首版。
- POSIX backend 实现（仅接口 + `BackendUnavailableError` 行为）。
- Agent takeover 的双 writer 改造（继续沿用现有 held/生命周期锁路径，后续单独设计）。
- 权威快照的“全保真”承诺：权威仿真器在 **runner（runtime 宿主）内持续运行**（候选 headless xterm +
  serialize，须先验证，见 §8），`fidelity/recovery` 字段如实上报；**浏览器不是权威**、其存活不参与任何
  恢复路径；快照引擎未验证或供料降级时，**不允许静默“全恢复”**（客户端被告知 `recovery=none|partial`）。

---

## 2. 目标架构

```
┌─ 浏览器（React + xterm.js/fit；仅渲染与输入） ─────────────────────────┐
│  TerminalPanel: 订阅 / 输入 / resize / ack / 用服务器快照重画           │
│  （浏览器不是权威仿真器：可随时不存在，不影响任何恢复能力）             │
└───────────────▲────────────────────────────────────────────────────────┘
                │ WS /ws/terminal/{term_id}（JSON 消息；Pan 入口授权）
┌───────────────┴────────────────────── Pan 服务进程 ────────────────────┐
│  packages/core/terminal/service.py  TerminalService                    │
│   ├─ TerminalRegistry（data/terminals/<id>.json，原子写+命名互斥）      │
│   ├─ IPC 凭证存取（DPAPI 秘密文件，见 §6；registry 不含 token）        │
│   ├─ AttachmentRegistry（control lease / generation / 撤销）           │
│   ├─ runner 管理器：派生、身份核验、reconcile、shutdown 确认           │
│   └─ WS 桥：游标分发、ack、gap→向 runner 要快照、exit 事件             │
│  lifespan: startup reconcile；shutdown 停非 detached、保留 detached     │
└───────────────▲────────────────────────────────────────────────────────┘
                │ IPC：\\.\pipe\pan-terminal-<term_id>
                │ （Named Pipe，owner-only DACL + token + 服务端身份核验；
                │   仅 Pan 服务与 runner 两端；浏览器永不接触端点或 token）
┌───────────────┴──────────── 每终端一个 runner 进程（detached）──────────┐
│  python -m packages.core.terminal.runner --terminal-id <id>            │
│   ├─ 出生持有 PTY（ConPTY，suspended spawn→assign→resume）             │
│   ├─ 自持 Job Object（KILL_ON_JOB_CLOSE）罩住 PTY 整树                 │
│   ├─ reader → ① OutputLog（有界 256 KiB，客户端游标/gap 用）           │
│   │           ② 权威仿真器（全量持续消费，runtime 生存期常驻）         │
│   ├─ lease：Pan 服务持有；丢失且未 detach → 统一死期后自停（§13）      │
│   ├─ detach 置位 → durable；显式 stop → 快照所有权→终止→残留核对       │
│   └─ 控制端点：命名管道 JSON-line（input/read/snapshot/resize/lease/stop）│
└─────────────────────────────────────────────────────────────────────────┘
```

数据流要点：

- runner 是**唯一的 PTY 属主**（布局 B：出生即拥有，不进任何服务级 kill-on-close Job；
  guard 句柄只归 runner 进程）。Pan 服务只持 lease 与控制连接，崩溃/重启都不改变 runner 树。
- runner 的 reader 把每个块**同时**交给有界 OutputLog（面向客户端游标）与**权威仿真器**
  （面向状态重建）；仿真器在 runner 内常驻，**没有浏览器也在消费**（浏览器的存亡与恢复能力无关）。
- Pan 服务不代理逐字节输出；WS 桥按游标拉取/推送，gap 或重连时向 runner 请求权威快照，
  快照与游标在 runner 内同一临界区原子产出（§8）。
- `TerminalRecord` 只存事实与提示（pid/创建时间/pipe 名/detached/scope/exit 事实），不存 token；
  IPC 凭证单独走受限秘密存储（§6）。

---

## 3. 模块与文件边界（新增/修改清单）

### 3.1 公共核心：新增 `packages/core/terminal/`（从契约原型迁移，去探针化）

| 文件 | 职责 | 备注 |
| --- | --- | --- |
| `__init__.py` | 导出公共名（`PtyBackend/OutputLog/PtyRuntime/TerminalRegistry/build_runtime/...`） | 无副作用导入 |
| `backend.py` | `PtyBackend` Protocol；`ConPtyBackend`（Windows 生产）；`WinptyBackend`（参照/回归对照，**不进生产门禁路径**）；`BackendUnavailableError` | 延迟导入 Windows 依赖 |
| `spawn_win.py` | `spawn_conpty_suspended(argv, cwd, env, rows, cols)`：`CreatePseudoConsole` + `InitializeProcThreadAttributeList` + `UpdateProcThreadAttribute(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE)` + `CreateProcessW(CREATE_SUSPENDED|EXTENDED_STARTUPINFO_PRESENT)` + `AssignProcessToJobObject` + `ResumeThread`；返回 `SpawnEvidence{pid, createTimeFiletime, assigned, atomic_with_spawn, guard}` | P0 先做 spike + 证据，再进 core |
| `guard.py` | `TreeGuard` Protocol；`JobObjectGuard`（生产：创建/assign/terminate/active/close/is_member）；文件头注明：psutil 方案仅测试观察，非生产 | 句柄由 runner 生命周期持有 |
| `identity.py` | `ProcessIdentity`（pid + 100ns FILETIME）；**存活判定 = 同一 handle 上 `WaitForSingleObject(h,0)`：`WAIT_TIMEOUT`=存活、`WAIT_OBJECT_0`=已退出**（不用 `GetExitCodeProcess==259`：259 既是 STILL_ACTIVE 也是合法退出码，语义歧义）；`kill_verified` 单句柄原子核验终止；`is_running` | 移植生命周期探针 `9bd858e2` 的单句柄原则，但存活原语改为 signaled 语义（探针原实现用退出码，故此处是有意升级，见 §14.4） |
| `output.py` | `OutputLog/OutputChunk/OutputPage/InvalidCursorError`（绝对偏移、整块驱逐、gap、无补零） | 契约 §2.2 |
| `runtime.py` | `PtyRuntime`、`RuntimeState`、`ExitInfo`、`CleanupReport`、`IllegalStateTransition`；drain 循环（EOF 才结束、`eof_grace`）；`close()` 顺序=快照所有权→可选中断→终止→残留核对→drain 收敛→join reader→关句柄 | 契约 §2.3 |
| `ownership.py` | `OwnershipPolicy/OwnershipMode`、`StartupOwnershipGate` 四要素、`build_runtime` fail-closed 工厂 | 布局中立，不按 `lifecycle_owner` 分支 |
| `registry.py` | `TerminalRecord/TerminalRegistry/UnknownTerminalError`；持久化、原子写、命名互斥（复制 `background_jobs` 模式，独立实现，**不改** `background_jobs.py`） | 目录 `data/terminals/` |
| `attachments.py` | `LeaseToken/AttachmentRegistry/StaleLeaseError/NotControlLeaseError`；撤销不可复活、同临界区校验+写 | 契约 §2.5 |
| `observer.py` | `ScreenObserver` Protocol、`ScreenSnapshot`、`PyteScreenObserver(fidelity="partial")` | 自动化观察专用 |
| `emulator.py` | **权威仿真器宿主**（runtime 生存期常驻 runner 内）：`AuthoritativeEmulator` Protocol（`feed(chunk)`/`snapshot() -> Snapshot{cursor, rows, cols, fidelity, recovery}`/`resize`/`reset`）；`HeadlessXtermEmulator`（候选：@xterm/headless + @xterm/addon-serialize 的 Node sidecar 桥，**须经验证**，见 §8）；`PyteEmulator`（降级模式，`fidelity=partial`、`recovery=partial`，**不得**当权威）；供料为有界队列 + 滞后标记（`feed_lag`），快照与 OutputLog 游标同一临界区原子读取 | 新增；spike 通过前不得进入 P3 的快照恢复验收 |
| `driver.py` | `AutomationContext`、`AutomationDriver`（`send_text/send_keys/screen_text/wait_for(quiet_ms)`） | 公共核心**零** CBC 字面量 |
| `ipc.py` | 帧协议（JSON-line）、消息类型常量、token 校验（`hmac.compare_digest`）、日志脱敏助手 | 与 `win_pipe.py` 解耦 |
| `win_pipe.py` | 命名管道 server/client（ctypes：`CreateNamedPipeW` + `FILE_FLAG_FIRST_PIPE_INSTANCE` + owner-only DACL：`GetTokenInformation(TokenUser)`→`SetEntriesInAclW`→`InitializeSecurityDescriptor/SetSecurityDescriptorDacl`；`GetNamedPipeServerProcessId` 读取对端 PID） | P1 spike 先行；非 Windows 抛 `BackendUnavailableError` |
| `secret_store.py` | DPAPI 秘密封装（ctypes `CryptProtectData/CryptUnprotectData`）：读/写/删 `data/terminals/secrets/<term_id>.secret`，owner-only ACL；bootstrap hello 文件读写；内容只在内存中解密 | §6.1；测试断言不落日志 |
| `runner.py` | runner 进程主逻辑（`python -m packages.core.terminal.runner --terminal-id <id> --secret-file <path>`）：持 PTY+guard、pipe 端点、**仿真器常驻与快照服务**、lease 监控、detach、stop、崩溃安全清理；启动自检（secret 解密、pipe 独占、门禁证据齐备） | 独立进程入口；argv 不含 token |
| `service.py` | `TerminalService`：create/list/get/close/detach/snapshot/input/resize；秘密文件的创建/读取/删除（§6）；runner 派生（`resolve_pan_python_argv` + `DETACHED_PROCESS`）；reconcile（含 shutdown/restart 状态机 §5.5）；WS 会话桥；lifespan 钩子 | Pan 进程内单例；无浏览器依赖 |

### 3.2 Pan 接线（修改既有文件，给出精确锚点）

| 文件 | 修改 |
| --- | --- |
| `packages/web/server.py` | 新增 REST：`POST /api/terminals`、`GET /api/terminals`、`GET /api/terminals/{id}`、`POST /api/terminals/{id}/close`、`POST /api/terminals/{id}/detach`、`POST /api/terminals/{id}/resize`（兼容入口）；新增 WS：`/ws/terminal/{terminal_id}`（结构参照现有 `/ws` L4687 与 `/ws/agent` L4864）；lifespan（L187-274）startup 调 `terminal_service.reconcile()`、shutdown 调 `await terminal_service.shutdown()` |
| `packages/mcp/server.py` | 新增 `terminal_create/list/get/close/detach/input/snapshot`（命名 `terminal_*`，与 `agent_background_*` 并行不混用） |
| `packages/web/src/` | `views/TerminalPanel.tsx`、`stores/terminal.ts`、`services/terminalWs.ts`、`types/terminal.ts`；挂载到现有路由/导航（React-only；改后 `pnpm build`） |
| `packages/web/package.json` | 新增 `@xterm/xterm`、`@xterm/addon-fit`、`@xterm/addon-serialize` |
| `minimal-requirements.txt` | 新增 `pywinpty==3.0.5 ; sys_platform == 'win32'`（CI 为 windows-latest，两版本矩阵不受影响） |
| PR6 合并后：`packages/core/rewind/driver.py` | `_PtySession`（L477-582）退化为 `PtyRuntime+ConPtyBackend` 薄封装；返回 `CleanupReport`；菜单逻辑（`navigate_to_anchor` L800-871、`_settled_selected_row` L781-797、`_await_restore` L180-231）迁至 `packages/core/rewind/automation.py`（driver 层），依赖 `AutomationContext` |
| **不改** | `packages/core/background_jobs.py`、`packages/core/background_runner.py`、`packages/core/takeover_job.py`（只读复用其模式） |

### 3.3 测试文件（新增）

| 文件 | 类型 | 覆盖 |
| --- | --- | --- |
| `tests/test_terminal_output.py` | 纯逻辑（跨平台） | 绝对偏移、驱逐/gap 数学、单块超容量、非法游标 |
| `tests/test_terminal_runtime.py` | ScriptedBackend（跨平台） | 状态机、EOF vs alive、eof_grace、结束原因分类、close 顺序、cleanup-failed 保留 owner |
| `tests/test_terminal_registry.py` | 纯逻辑 | 原子写、锁、失败状态不可 remove、scope 字段 |
| `tests/test_terminal_lease.py` | 纯逻辑 | generation/撤销/迟到消息/observer 越权 |
| `tests/test_terminal_spawn_gate.py` | Windows + 真实 PTY | 原子 spawn 证据（suspend 期间不可见副作用）、assign 注入失败→拒绝 running、清理身份核验拒杀 |
| `tests/test_terminal_runner_ipc.py` | Windows | pipe DACL 负例（非 owner 拒绝）、错误 token 拒绝、日志无 token |
| `tests/test_terminal_service.py` | Windows，隔离数据根 | create/close/detach/崩溃 lease/重连同 PID（服务层） |
| `tests/test_terminal_emulator.py` | Windows，真实 PTY + Node sidecar（spike） | 权威仿真器保真矩阵（主/备屏、模式、滚动、resize）、供料滞后降级语义、快照 `cursor/rows/cols` 原子一致性、无浏览器常驻消费 |
| `tests/test_terminal_api_ws.py` | TestClient + mock service | WS 协议、ack、gap→快照、Origin 校验、无权连接拒绝 |
| `tests/test_terminal_e2e.py` | `-m terminal_e2e`，隔离 Pan 子进程 | §11 验收矩阵 |
| `tests/test_rewind_*.py`（PR6 原 7 文件 62 项） | 保留并改接入 | §10 |

---

## 4. 共享 runtime 与现有 Pan Job 的职责

| 维度 | Pan Job（`background_jobs`，不改） | Terminal（本计划） | 共享方式 |
| --- | --- | --- | --- |
| 对象语义 | 任务/结果对象（argv→日志→终态通知） | 交互进程端点（PTY↔多连接） | 不混同；MCP/API 命名分离 |
| 默认寿命 | durable（跨 Pan 存活） | 随 Pan 生死（显式 detach 才 durable） | 策略参数化，**不复用其默认** |
| runner 进程 | `packages/core/background_runner.py` | `packages/core/terminal/runner.py` | 各自独立实现；Terminal 不塞新 kind 进 Job 注册表 |
| 注册表 | `data/background_jobs/`（JSON+命名互斥+原子替换） | `data/terminals/` | 模式复制：命名互斥、原子写、pid+FILETIME 身份核验（代码独立，避免耦合） |
| 身份核验 | `_owns_process`（psutil 容差 1s） | `identity.py`（精确 FILETIME + 单句柄原子终止） | Terminal 采用更强语义；未来可选把该工具提为公共模块（不在首版） |
| 恢复 | `reconcile_running`：存活保留/否则 failed | 同原则 + detached 保留并 re-attach | 语义对齐、实现独立 |
| Windows Job Object | 不用（detached 进程组） | runner 自持 guard job（整树） | 两者是不同概念（审查 §边界），互不替代 |

明确边界：**不**把 Terminal 作为 `background_jobs` 的新 `kind`；**不**改 `background_runner` 的寿命语义；
若未来需要统一，前提是先抽公共 `runtime owner` 接口并单独立项。

---

## 5. 所有权、生命周期与失败门禁

### 5.1 布局（已定方向）

- **布局 B（推荐，已实测语义）**：runner 出生即拥有 PTY；runner 自持 `KILL_ON_JOB_CLOSE` Job 罩住
  shell 整树；Pan 服务只持 lease + 控制连接。runner 不在任何服务级 kill-on-close Job 里（否则不可 detach）。
- 句柄移交布局 C（`DuplicateHandle`）与 ConPTY host/IO 迁移：**不进首版**（维持契约校准结论）。

### 5.2 生命周期表（实现必须逐条对齐验收）

| 事件 | 默认终端（managed） | 已 detach 终端 |
| --- | --- | --- |
| 浏览器断开/刷新 | 无变化（仅释放 WS；快照由 runner 常驻仿真器维持，见 §8） | 无变化 |
| Pan 正常关闭 | 记录先转 `stopping` → 服务发 `stop` → **等待 runner 确认整树退出（有界，默认 5s）** → 记录 `exited(reason=service_shutdown)`；未确认的保留 `stopping(cleanup_failed)` 并列入下次 reconcile，**不伪标 `lost`** | 服务 `EXIT_KEEP`：不 stop、撤销 lease、秘密与记录标记 `detached`；runner 仿真器与 PTY 继续存活 |
| Pan 崩溃 | lease 丢失，统一死期（默认 2s，见 §13）后 runner 自停并整树清理；重启后 reconcile 按身份核验把已死 runner 记为 `exited(reason=lease_expired)` | 忽略 lease 丢失，继续运行；重启后秘密文件可解密 → re-attach |
| 显式关闭终端 | 同上 `stop` 路径（杀整树），删除秘密文件 | 同左（显式操作） |
| runner 崩溃/被硬杀 | guard 句柄由内核关闭 → 整树死 | 同左（树死；registry 记录转 `exited`/`lost`，reconcile 不伪造存活） |
| Pan 重启（含快速重启） | 见 §5.5 状态机：`stopping` 未确认者继续确认→`exited`；身份可核验但仍在运行的 managed runner 属异常，标 `cleanup_failed` 并重试 stop，**不标 lost、不遗弃进程**；身份不可核验才 `lost` | reconcile：身份核验（pid + FILETIME，同一 handle）后 re-attach lease/控制；不可核验→标 `lost`，**不杀进程**；秘密跨重启可解密（§6.1） |
| 机器重启 | OS 语义：进程族不存活；registry 一律标 `exited`，不伪造同 PID | 同左 |

### 5.3 生产原子 spawn（门禁必做，方案与替代）

契约门禁四要素：`assigned`、`atomic_with_spawn`、`identity`、`handle_bound_for_cleanup`，
任缺一即拒绝 `running`（fail-closed）。`pywinpty.spawn` 不暴露 creationflags（生命周期探针已记录），
因此**不能**用“先 spawn 再 assign”作为生产路径。可行替代（按推荐序）：

1. **自研 suspended ConPTY spawn（首选）**：`spawn_win.py` 用官方 ConPTY 序列：
   `CreatePseudoConsole` → `STARTUPINFOEX` + `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` →
   `CreateProcessW(CREATE_SUSPENDED|EXTENDED_STARTUPINFO_PRESENT)` → `AssignProcessToJobObject`（guard）
   → `ResumeThread`。原子性即“进程从未在未入组状态下执行”；子进程天然继承 guard。
   参照：官方 "Creating a Pseudoconsole session"；`takeover_job.py` 的 suspend/assign/resume 已证明该模式可行。
2. **Bootstrap 兜底**：若首版暂不做 ConPTY 自研（例如时间不足），经门禁拒绝 `running` 是唯一合法降级——
   **不允许**静默走“spawn→assign”。因此计划默认按 1 做；2 仅作为评审时被否决的回退说明。
3. 失败门禁行为（s6 语义移植）：assign 失败/证据不齐 → 不发布 running、不开控制端点、
   仅清理自有后代（身份核验）后非零退出；服务侧将创建请求返回明确错误并回收记录。

### 5.4 reconcile 与身份

`TerminalRecord`：`terminal_id / pid / processCreatedAtFiletime / pipe / detached / detachedAt /
scope{workspace_id, session_id} / status / exit{code, reason} / leaseGraceSeconds / createdBy`。
reconcile 只信 `pid + FILETIME`（**同一 handle 上 `WaitForSingleObject` 判存活**：`WAIT_OBJECT_0`=已退出，
`WAIT_TIMEOUT`=存活，见 §3.1 `identity.py`）；不认识/不匹配一律 `lost`；绝不按 PID 单值杀进程，
也不以退出码 259 作为存活/死亡判据。

### 5.5 Pan shutdown / restart 状态机（不伪标 lost、不漏进程）

```
managed 记录状态：running ──(服务开始关闭)──► stopping ──(runner 确认整树退出)──► exited
                        │                        │
                        │                        └─(有界等待超时/未确认)──► stopping(cleanup_failed)
                        └─(服务崩溃，无 stopping 记录)──► 由 reconcile 处理

reconcile（新 Pan 启动，逐条）：
  1) status=stopping / cleanup_failed：
       身份核验（pid+FILETIME，同一 handle）
         ├─ 进程已退出（WAIT_OBJECT_0）      → exited(reason=service_shutdown)
         ├─ 进程仍存活且记录非 detached      → cleanup_failed：重发 stop（有界重试），不标 lost
         └─ 身份不可核验（pid 不存在/被复用）→ exited（若属正常关闭窗口）或 lost（无法归因）
  2) status=running（上次为崩溃）：
       非 detached：等统一死期（默认 2s）+ 缓冲 → 复核；
         ├─ 已退出 → exited(reason=lease_expired)
         └─ 仍存活（异常）→ cleanup_failed + 重发 stop；不伪造 exited/lost
       detached：身份核验后 re-attach（lease/控制/快照），秘密文件可解密（§6.1）
  3) 任何分支：绝不对"身份不可核验"的 PID 执行杀进程；失败只记录并在 UI 暴露重试。
```

快速重启（旧 Pan 退出未完成即新 Pan 启动）时，新 Pan 不新建同 `terminal_id` 的 runner：
pipe 独占（`FILE_FLAG_FIRST_PIPE_INSTANCE`）+ registry 记录共同保证单实例；旧 runner 由
`stopping`/`cleanup_failed` 分支收敛，**不产生幽灵进程、不丢记录**。

---

## 6. IPC 身份认证、凭证恢复与 Web 授权

### 6.1 传输、凭证与跨 Pan 重启恢复（单一首选方案）

**首选方案：DPAPI 用户级秘密文件 + pipe 独占 + 服务端身份核验。** 详细：

1. **秘密存储（唯一权威副本）**：`data/terminals/secrets/<term_id>.secret`，写盘前用 Windows DPAPI
   *用户作用域*（ctypes `CryptProtectData`/`CryptUnprotectData`）加密，文件 ACL 仅当前用户；
   内容 = `{token(32B), runnerIdentity{pid, createTimeFiletime}, pipeName, terminalId}`。
   **公开 registry（`data/terminals/*.json`）、日志、WS 一律不含 token**（registry 至多记录
   `secret_file` 路径与 `pid+FILETIME` 事实）。
2. **首次 bootstrap（无秘密 → 有秘密；规避 spawn PID 错配）**：
   ① 服务 spawn runner：`--terminal-id <id> --secret-file <path>`（**argv 只有路径，无 token**）；
   ② runner 启动后先写 `<path>.hello`（内容 = 自身 `pid + createTimeFiletime`，来自 `GetProcessTimes`），
      然后**等待秘密文件出现**（有界 10s，超时退出非零），期间不创建 pipe、不接受任何连接；
   ③ 服务读取 hello（不依赖 Popen PID，免受解释器包装层影响），生成 token，写 DPAPI 秘密
      `{token, runnerIdentity(hello), pipeName, terminalId}`；
   ④ runner 检测到秘密 → 解密 → 比对自身 `pid+FILETIME` 与文件 identity，不匹配即 fail-closed
      （不监听、退出非零；删除自己未完成的 hello）；匹配后才以 `FIRST_PIPE_INSTANCE` 创建 pipe 并进入服务循环。
3. **跨 Pan 重启 / detach 后新 Pan**：新 Pan 同为用户进程 → 可解密同一秘密文件 → 取得 token 与
   runner 身份 → 直接重连旧 runner。**凭证恢复不依赖旧 Pan 存活**（这正是 detach 的闭环条件）。
4. **防 pipe 冒充（squatting / PID 复用）**：
   - runner 用 `FILE_FLAG_FIRST_PIPE_INSTANCE` 创建管道（名字被占即失败退出）；
   - 服务每次连接后调用 `GetNamedPipeServerProcessId` 取得**对端真实 PID**，再以
     `OpenProcess + GetProcessTimes` 比对**同一 handle** 的 `pid + FILETIME` 与秘密/registry
     中的 runner 身份（`WaitForSingleObject` 判存活）——不一致立即断开；
   - token 校验 `hmac.compare_digest`；两者同时通过才接受该连接为 runner。
5. **威胁模型如实声明**：DPAPI 用户作用域与文件 ACL 只隔离**其它 Windows 用户**；同用户进程
   理论上均可解密/复制 token（契约 M14.19 的同类边界）。首版信任边界=同一 Pan 用户，
   不做"防同用户恶意软件"的承诺；该边界必须写进用户文档。
6. **生命周期**：显式关闭终端成功终止后删除秘密文件；detach 状态保留；`lost` 记录保留文件
   （供人工排查）但在 UI 标明不可信；机器重启后统一按 `exited` 处理并清理秘密。
7. 非 Windows/开发降级：loopback TCP + 同一 token 机制（仅原型/测试）；生产 Windows 路径必须走 pipe。

### 6.2 不泄露规则（实现硬约束 + 测试断言）

1. token **不得**出现在：日志、`data/terminals/*.json`、错误消息、HTTP/WS 响应体、URL query、
   异常回溯字符串、argv/环境变量；日志仅打 `terminal_id` 与 `client_id`。
2. 浏览器**永不**接触 runner token 或 pipe 名：前端只用 `terminal_id` + Pan 会话；服务端 WS 桥持有两侧。
3. runner/service 崩溃日志同样脱敏（runner 启动参数只含 `--secret-file` 路径）。
4. 测试断言（`test_terminal_runner_ipc.py`）：以注入哨兵 token 反查 runner/service 日志、registry JSON、
   argv 快照（`Get-CimInstance`/`wmic` 不可用时用 spawn 侧记录）均无命中；解密失败路径不打印内容。
5. 凭证分层：runner IPC token 只用于 Pan↔runner 身份认证，**不是**浏览器 attachment lease。
   `attachments.py` 的控制权 lease 使用独立 `revocation_id/generation`；撤销后旧 lease 的
   `send/resize/transfer` 均 `StaleLeaseError`（契约 M14）。浏览器不得得到 IPC token。

### 6.3 Web 入口信任边界与授权（不能只靠“服务校验”）

**现有边界（事实）**：Pan 默认只绑 `127.0.0.1`（`main.py` 对非 loopback 有"无鉴权"警告）；
当前 API/WS 无用户认证。终端**不引入新的鉴权体系**，但必须把入口检查显式化，并明确
"知道 `terminal_id` ≠ 权限"：

1. **浏览器路径的入口检查（终端专属，不改既有端点行为）**：
   - WS `/ws/terminal/{id}`：校验 `Origin` 白名单（默认 `http://127.0.0.1:<PAN_PORT>` /
     `http://localhost:<PAN_PORT>`，可配 `PAN_TERMINAL_ALLOWED_ORIGINS`）；`Origin` 缺失或不在
     白名单即拒绝握手（浏览器对 WS 不受 CORS 限制，必须显式检查）。
   - 终端 REST（create/close/detach/resize）：校验 `Origin`/`Sec-Fetch-Site`（拒绝跨站
     `cross-site`/`none` 的简单请求），防 CSRF 触发的状态变更；同时保持方法语义
     （创建/关闭等一律 POST）。
   - 所有终端 REST/WS 仅绑定 loopback 监听；`PAN_HOST` 非 loopback 时终端 API 默认**不启用**
     （除非显式 `PAN_TERMINAL_ALLOW_REMOTE=1` 且用户自担暴露风险），与既有 LAN 警告一致。
2. **MCP 路径的调用者身份与 scope 绑定**：MCP 走本机 stdio/客户端进程边界；`terminal_*` 工具
   记录 `createdBy`（MCP 会话/客户端标识或工具调用上下文提供的 `session_id`），并接受可选
   `workspace_id/session_id` 作为**上下文标注**。**该标注不是权限**：首版所有终端同用户全局可见，
   scope 仅用于列表过滤/展示与审计（避免"看起来像隔离、实际无校验"的误导）。
3. **权限策略（首版明确声明）**：全局 Terminal = "同一 Pan 实例、同一 Windows 用户、经入口检查
   （本地网页 Origin 白名单 / MCP 本机进程）"。`terminal_id` 只是对象标识，任何 API/WS 都必须在
   `TerminalService` 内完成上述入口检查后才返回数据或接受输入；服务内不做"按 session/workspace
   分区鉴权"（无多用户模型可依）。
4. 关联 Workspace/Agent Session 仅元数据：会话删除/转移不改变终端所有权与可选授权面；UI 文案
   必须避免暗示"关联即授权"。

---

## 7. 输出管道：reader EOF、背压、gap、resize

### 7.1 reader 与结束原因（契约 §2.3 落地）

- reader 循环只看 `read()` 抛 `EOFError`（或通道错误）才结束；`alive()=false` **不是**结束条件；
  空读且进程已退出 → 有界 `eof_grace`（默认 8s，令参数）。
- 三事实分开：`process_exit_seen/code`、`reader_done`、`channel_eof`、`output_complete`；
  `drain_stop_reason ∈ {eof, cancelled, channel-error, eof-timeout, stop-requested}`；仅 `eof` 允许 `output_complete`。
- 取消：pywinpty/ConPTY 的“关句柄取消”会连带终止进程（R9 实测）——`close()` 仅在终止+整树成功后才取消；
  取消后必须证明 `reader_converged` 才允许 `exited`，否则 `cleanup-failed` + 保留 owner。

### 7.2 背压（不让慢客户端拖住任何东西）

- runner 只在 `OutputLog`（默认 256 KiB）内保留；**无**每客户端队列；慢客户端游标落到窗口之前
  即返回 `gap=(cursor, first_retained_seq)`，由客户端走**服务器权威快照**恢复（§8）。
- WS 桥推送带 `nextSeq`；浏览器在渲染完成后上报 `ack` 游标；服务端对每连接设有界发送队列
  （默认 4 MiB），超限断开该连接（其它连接与 PTY 不受影响）。
- PTY reader 永不因客户端阻塞：它只追加到有界日志。
- **权威仿真器供料（与客户端背压无关）**：reader 每块在**同一临界区**内 ①追加 OutputLog、
  ②投递给仿真器（有界队列，默认 4 MiB / 8192 块）。仿真器只更新状态（内存有界：屏幕+滚动），
  供料速度必须实测，不能假设仿真器永远跟得上；若队列打满（仿真器停滞）= `feed_lag` 置位，此时**禁止**声称权威状态完整
  （§8 降级语义），而不是阻塞 PTY reader 或悄悄丢字节。

### 7.3 gap 与 resize 同步

- gap 语义（契约 R4/R7、M15）：不补零；窗口起点可能落在 UTF-8/CSI/OSC 中间——
  客户端**不得**从窗口起点解析，必须快照恢复。
- resize：仅 control lease 可通过 `PtyRuntime.resize(rows, cols)` 修改；同时动作到
  ① ConPTY（`ResizePseudoConsole`）② **权威仿真器** ③ observer ④ 客户端 fit addon；
  旧 generation 的 resize 被拒。尺寸跟随当前控制客户端；快照在 runner 内与 resize 同一临界区
  产出 `(cursor, rows, cols)` 三元组，保证新观察者拿到的屏幕与尺寸一致（§8）。

---

## 8. 权威快照：runner 内常驻仿真器（浏览器不是权威）

### 8.1 架构（修正：快照权威在服务端/独立 host）

- **权威仿真器位于 runner（runtime 宿主进程）内，随 runtime 生存期持续运行**：reader 的每个块
  （含所有浏览器关闭期间）都同步投递给仿真器；浏览器只是渲染器，其打开/关闭/崩溃与恢复能力无关。
- 候选引擎：**Node sidecar + @xterm/headless + @xterm/addon-serialize**（runner 生命周期内常驻，
  stdin/pipe 供料、请求-响应式取快照）。**候选必须先在 P1 spike 中验证**（保真矩阵：主/备屏、
  模式、光标、滚动、resize；对照真实 `less.exe` TUI）。验证通过前，P3 的“快照恢复”验收**不得开工**。
- 降级引擎：`PyteEmulator`（`fidelity=partial`、`recovery=partial`）仅用于无图形/自动化观察或
  sidecar 不可用时的**显式降级**，且必须在 API/UI 上暴露，**不得**作为权威或“完整恢复”来源。
- `PyteScreenObserver` 仍只服务自动化（PR6 driver、测试），与权威快照是两条通道。

### 8.2 供料与 gap：权威状态不得丢字节

- 供给路径：reader 临界区内 `OutputLog.append + emulator.feed`；两者同序。
- **有界供给语义**：feed 队列有界（默认 4 MiB / 8192 块）。仿真器更新是纯状态计算（无网络），
  是否积压由实测与运行时观测决定；若打满 → `feed_lag=true`：
  - **不阻塞 PTY reader**（避免拖死终端）、**不静默丢字节**（不假装快照仍完整）；
  - 进入 `recovery=degraded`：此后所有快照声明“可能不含完整历史”；客户端收到后必须
    **显式重打基线**（`reset-baseline`：清屏后仅保证从此 cursor 起的未来输出），UI 明确提示；
  - `feed_lag` 恢复后不自动“补全”——需要人工/客户端显式请求新基线，避免伪造恢复。
- **客户端 gap ≠ 权威 gap**：OutputLog 的 256 KiB 驱逐只影响游标拉取；权威状态不因驱逐丢失。
  `gap` 出现时客户端取**当前权威快照**（cursor = 仿真器确认已应用的 `applied_seq`），再从其后续接流。
  若该 cursor 已落在日志窗口之前，必须重取更新的快照或显式降级；不能直接跳到日志窗口起点。

### 8.3 快照原子性与跨 detach/Pan 重启

- 快照 = `{serialized_screen, cursor(=applied_seq), rows, cols, fidelity, recovery}`。
  **reader 入队不等于 Node sidecar 已解析**：Python 临界区只能保证投递顺序，不能证明跨进程屏幕一致。
  feed、resize、snapshot/barrier 必须走同一有序命令通道；sidecar 在 xterm write 回调完成后确认
  `applied_seq`，并在 barrier 上原子返回屏幕、已应用游标与实际尺寸。禁止用 producer 的
  `total_bytes` 替换 snapshot cursor；等待 barrier 有界超时即返回明确不可恢复状态，不能阻塞 reader。
  UTF-8 跨块、CSI/OSC 解析器残留也属快照一致性：须证明序列化包含状态，或选择安全解析边界，
  未证明时不能报告 full。客户端渲染完成后再 ack 该 cursor。
- detach、Pan 正常退出、Pan 崩溃重启：仿真器在 runner 内随 PTY 一起存活 → **新 Pan/新浏览器
  直接取得同一权威快照**；凭证恢复见 §6.1。runner 硬杀时树与仿真器一并消亡（记录转终态，不伪造恢复）。

### 8.4 诚实性约束（产品承诺）

- 只有 `fidelity=full 且 recovery=full` 的快照才允许对用户表现为“完整恢复”。
- 引擎未验证、sidecar 缺依赖、`feed_lag`、`cleanup-failed` 等任一情形 → 快照按
  `recovery=partial|none` 返回，客户端执行 **fresh-view/重打基线**，不得静默“看起来恢复了”。
- 验收矩阵按 §11 T3/T7/T17/T19 执行；未覆盖的模式在 UI 与文档中列明。

### 8.5 MA 接口冻结补充（2026-10-03）

- P0 先冻结平台无关 Protocol 与公共数据结构；新增 `contracts.py` 由核心 TA 独占，
  Windows backend 在接口冻结后实现它，不反向引入 Windows 依赖到纯逻辑核心。
- 仿真器 spike 必须覆盖异步 feed 后立即 snapshot、feed/resize/barrier 交错、UTF-8/CSI/OSC
  跨快照边界、日志窗口再次驱逐、溢出后显式基线重置；结果不足则明确 partial/none。
- MCP 接线沿用既有 caller 解析及能力检查；`createdBy` 只能来自经过核验的调用上下文，
  不从目标 `session_id` 推断调用者，也不以“同用户全局”绕过已有工具权限边界。

---

## 9. Windows 优先与 POSIX 边界

- `PtyBackend` 为唯一平台原语面；Windows 生产实现 `ConPtyBackend`（自研 spawn，见 §5.3）。
- `pywinpty` 仅作为**参照回归实现**（`WinptyBackend`）与兼容测试，不在生产门禁路径；
  依赖在 `minimal-requirements.txt` 加 `sys_platform == 'win32'` 标记，非 Windows 平台安装不被阻断。
- POSIX：首版只有 Protocol 与 `BackendUnavailableError`（创建终端时报可执行提示），不实现
  `PtyBackend`；不把 Windows 假设泄漏进 `runtime/output/registry/attachments`（纯逻辑模块保持跨平台可测）。
- CI：现有 `test.yml` 已是 windows-latest × Python 3.12/3.14；纯逻辑测试两版本都跑，
  PTY 测试用 `pytest.mark.skipif(sys.platform != "win32")` 显式标注。POSIX 真机测试延后。

---

## 10. PR #6 回滚接入（原 62 项测试）

- 接入方式（契约 §3 映射表落地）：`_PtySession` → `PtyRuntime + ConPtyBackend` 薄封装；
  `rewind()` 菜单逻辑抽到 `packages/core/rewind/automation.py`（driver 层，经 `AutomationContext`）；
  `driver.py` 不再直接触碰 `proc` 与 `queue`。
- 必须保持的对外语义：回滚确认/进度、fork+resume 流程、返回结构、错误路径；
  `finally` 路径改为 `CleanupReport` 语义（`exited` vs `cleanup-failed`，失败不谎报）。
- 测试接入步骤：
  1. PR6 代码先并入集成树（MA 已管理），测试文件原样保留；
  2. 运行 `python -m pytest tests/test_rewind_*.py -q`（7 文件，审查基线 **62 passed**）；
  3. 分类失败：仅断言 `_PtySession` 私有细节的用例做**最小适配**（例如注入 fake backend），
     行为级用例必须无改动通过；
  4. 新增回滚 E2E 用例（真实 shell + 假 fork 结果注入）验证 `cleanup-failed` 不谎报。
- 门槛：62 项全通过 + 新增 E2E 通过，才允许该阶段合并；不通过则回退到“公共核心独立可用、PR6 暂缓接入”。

---

## 11. 隔离端到端验收矩阵（实现阶段执行；全部使用临时数据根与空闲 loopback）

前置：`PAN_TERMINALS_DIR=<tmp>`、`PAN_BACKGROUND_JOBS_DIR=<tmp>`、Pan 子进程 `PAN_PORT=<free>`、
不开真实 Agent 会话、不触发任何模型调用。证据：每用例落 JSON（断言+进程身份+时间线）到
`audit/terminal/implementation/evidence/`（实现阶段目录，不在本计划交付）。

| ID | 场景 | 隔离方式 | 必须观测 |
| --- | --- | --- | --- |
| T1 | Workspace 目录新建终端 | 临时 workspace 目录 | 真实交互式提示符；cwd 正确；记录 pid+FILETIME |
| T2 | 基础输入 | 真实 shell | 中文/长命令/方向键/Tab/Ctrl-C/Ctrl-D/粘贴（含 bracketed paste）往返正确 |
| T3 | TUI 与 resize | 真实 `less.exe` 等系统 TUI | 备用屏切换；resize 后子控制台尺寸变化（对 ConPTY 用 API 断言）；快照尺寸同步 |
| T4 | 大量输出+慢客户端 | 生成 ≥1 MB 输出；一个客户端暂停 ack | 保留量 ≤ 上限；gap 明确；PTY 不阻塞；其它连接不受影响 |
| T5 | 自然退出 | 短命令（尾输出+非零退出码） | 先交付尾部输出，再 `output_complete+code`；reader 收敛 |
| T6 | 关面板/断连保 PID | 断开 WS | shell PID/变量(cwd、set 变量)/运行中程序保持 |
| T7 | 刷新重连 | 同 terminal_id 新 WS | **服务器权威快照**（cursor/rows/cols 一致）恢复屏幕与模式；无重复输入（lease 校验 + ack 游标） |
| T8 | 显式关闭 | `POST /close` | 整树退出（含 2 层孙进程）；失败保留可重试状态；秘密文件删除 |
| T9 | detach + Pan 正常退出 | detach 后优雅退出 Pan | 同 PID/FILETIME 存活；新 Pan/客户端用 reconnect_hint 重连成功 |
| T10 | 默认 + Pan 崩溃 | 杀 Pan 进程（隔离实例） | 统一死期后 runner 自停、整树清理；detach 的终端不受影响 |
| T11 | Pan 重启 reconcile（含快速重启） | 重启隔离 Pan | managed 按 §5.5 收敛为 `exited`（**不伪标 lost、不漏进程**）；detached 核验身份后 re-attach；不杀不可核验进程 |
| T12 | runner 硬杀 | 身份核验后终止 runner | guard 句柄关闭→整树 0.6s 级清理；registry 转终态 |
| T13 | pipe 冒充/错误 token | 占名 pipe 或错 token 连接 | 服务校验 `GetNamedPipeServerProcessId` + 同 handle FILETIME 身份与 token，全部拒绝；日志无 token |
| T14 | assign 失败门禁 | 注入 assign 失败 | 不发布 running/不开端点；仅清理自有后代；API 返回明确错误 |
| T15 | 迟到输入/resize | 控制权转交后旧客户端重放 | `StaleLeaseError` 拒绝，终端状态不变 |
| T16 | PR6 回滚回归 | 集成树 | 62 项通过 + 回滚 E2E 通过（必做验收） |
| T17 | **Browserless 恢复** | 创建后不打开任何浏览器，产出 > 256 KiB（触发游标窗口驱逐）再打开浏览器 | runner 内仿真器全程消费；新浏览器拿到的权威快照仍含被驱逐前的屏幕状态；`recovery=full`（引擎已验证） |
| T18 | shutdown/快速 restart | 正常关闭后立即重启隔离 Pan | 旧 managed 全部收敛 `exited`；无幽灵 runner/pipe；状态无 `lost` 误标；记录与进程一一对应 |
| T19 | 供料降级诚实性 | 注入仿真器停滞（打满 feed 队列） | `feed_lag` → 快照 `recovery=degraded`；客户端收到显式重打基线提示；**无静默全恢复** |
| T20 | detach 凭证跨 Pan 恢复 | detach → 杀 Pan → 新 Pan（同用户）启动 | DPAPI 秘密解密成功、身份核验通过、re-attach 同 PID/FILETIME；伪造 pipe 被 T13 覆盖 |
| T21 | Web 入口防护 | 跨站 Origin 请求 / 无 Origin WS / 非 loopback 绑定 | WS 握手拒绝；REST 状态变更拒绝；`PAN_HOST` 非 loopback 时终端 API 默认不可用（除非显式开关） |

---

## 12. 阶段划分与并行 TA 职责（未来派发；本计划不派任何子代理）

### 12.1 阶段与出口标准

| 阶段 | 内容 | 出口标准 |
| --- | --- | --- |
| **P0 契约落地（无 UI/无服务接线）** | `output/runtime/registry/attachments/observer/driver/ownership` + ScriptedBackend 测试 | `tests/test_terminal_output|runtime|registry|lease` 全绿；接口冻结 |
| **P1 Windows 生产 backend** | `spawn_win` spike（先证据）→ `ConPtyBackend`；`guard/identity/win_pipe/ipc`；秘钥存储（DPAPI）；**权威仿真器 sidecar spike 与保真验证**（§8.1）；`runner.py` 进程闭环；门禁四要素 | `test_terminal_spawn_gate/runner_ipc/emulator` 全绿；原子 spawn 证据；s6 语义移植证据；仿真器 `fidelity` 矩阵通过（否则 P3 快照恢复不得开工） |
| **P2 服务层与 API** | `service.py`、REST/WS（含 Origin 检查）、MCP、lifespan 接线、reconcile、shutdown 确认 | T1–T2、T5–T6、T9–T14、T18、T21 通过（隔离实例） |
| **P3 前端** | xterm.js 面板、重连/gap/ack、服务器快照重画 | T3、T4、T7、T17、T19 通过；`pnpm build` 通过；hook TS 检查通过 |
| **P4 PR6 接入** | `_PtySession` 替换、菜单迁 `automation.py`、62 测试 | §10 门槛全过 |
| **P5 加固与最终验收（必做，不可标可选）** | 管道 DACL/身份核验压测、lease 抖动与统一死期、**撤销语义**（M14 全套）、**cleanup 语义**（cleanup-failed/重试/整树/秘密清理）、shutdown/restart 边界、文档 | T15、T16、T18、T20 重新验收全绿；安全（DACL/Origin/token 不泄露）、撤销、cleanup、PR#6 62 项四项**必须**在 P5 出口复验一次；`docs/USER_MANUAL` 增补 |

### 12.2 并行 TA 切分（无相互阻塞的界面）

| TA | 职责 | 依赖 | 写文件范围（互斥） |
| --- | --- | --- | --- |
| TA-A 核心逻辑 | `output/runtime/registry/attachments/observer/driver` + 纯逻辑测试 | 契约原型（只读） | `packages/core/terminal/{output,runtime,registry,attachments,observer,driver}.py`、`tests/test_terminal_{output,runtime,registry,lease}.py` |
| TA-B Windows backend | `spawn_win/backend/guard/identity/win_pipe/secret_store/ipc/runner` + **权威仿真器 sidecar（emulator.py，spike+保真验证）** + 门禁/保真测试 | A 的接口签名冻结 | `packages/core/terminal/{spawn_win,backend,guard,identity,win_pipe,secret_store,ipc,runner,emulator}.py`、`tests/test_terminal_{spawn_gate,runner_ipc,emulator}.py` |
| TA-C 服务与 API | `service.py`（含秘密存取与 shutdown 确认）、`server.py`（含 Origin 检查）、`mcp/server.py`、lifespan、隔离 E2E | A+B（可用 mock runtime 先行） | `packages/core/terminal/service.py`、`packages/web/server.py`（终端段落）、`packages/mcp/server.py`（终端工具）、`tests/test_terminal_{service,api_ws,e2e}.py` |
| TA-D 前端 | 面板/WS 客户端/快照/ack | WS 协议冻结（C 提供 mock） | `packages/web/src/**`、`packages/web/package.json` |
| TA-E PR6 接入 | rewind 改造与测试接入 | A+C；PR6 已在集成树 | `packages/core/rewind/**`、`tests/test_rewind_*.py` |

并行规则：接口先冻结（本文档 §2/§3 即冻结基线，变更走记录）；共享文件一次只允许一个 TA 写；
每阶段结束由 MA 在集成树做一次合并与验收，验收命令固定（§11）。

---

## 13. 工程默认参数（建议默认，实施可微调；无需再问用户）

| 参数 | 默认 |
| --- | --- |
| `read_size` | 64 KiB |
| `OutputLog.max_bytes` | 256 KiB/终端（对齐契约实测上限 262144；只看客户端游标，不是权威状态） |
| `eof_grace` | 8 s |
| **lease 统一死期** | **2 s（唯一口径）**：心跳间隔 1 s；`now - last_heartbeat ≥ 2 s` 即判丢失并自停（容忍 1 次心跳丢失；`1s×3` 口径废弃） |
| 权威仿真器 | runner 内常驻（候选 @xterm/headless + @xterm/addon-serialize Node sidecar，pin 版本）；feed 队列 4 MiB / 8192 块；打满 → `feed_lag` 降级（§8） |
| shutdown 确认 | `stop` 发出后等待 runner 整树退出，默认 5 s；超时转 `stopping(cleanup_failed)` |
| WS 每连接发送队列 | 4 MiB，超限断开 |
| 快照 | runner 原子快照（serialized_screen + cursor + rows + cols + fidelity + recovery）；滚动深度默认 1000 行 |
| token | `secrets.token_hex(32)`；`compare_digest`；存于 DPAPI 秘密文件（§6.1）；rotate=每次 detach/re-attach 可选（默认不 rotate） |
| 秘密文件 | `data/terminals/secrets/<term_id>.secret`（DPAPI 用户作用域 + owner-only ACL）；argv 只传路径 |
| 管道名 | `\\.\pipe\pan-terminal-<term_id>`；DACL=owner SID；`FILE_FLAG_FIRST_PIPE_INSTANCE` |
| registry | `data/terminals/`（`PAN_TERMINALS_DIR` 可覆盖）；原子写+命名互斥；**不含 token** |
| 默认 shell | `cmd.exe /q /d`；`terminal.shell` 配置可换 `pwsh.exe` |
| 终端数量软上限 | 8（超出返回明确错误） |
| 清理顺序 | 快照所有权→可选中断→终止→残留核对→drain 收敛→join reader→关句柄（身份用同 handle signaled 判定） |

---

## 14. 依赖、风险与自查

### 14.1 依赖顺序（硬约束）

```
P0(A) ──► P1(B) ──► P2(C) ──► P3(D) ──► P4(E) ──► P5(必做最终验收)
A 接口冻结是 B/C/D 的前置；B 的原子 spawn 证据是任何 “running 终端” 验收的前置；
B 的权威仿真器保真验证是 P3 快照恢复验收的前置（未验证 → P3 只能做“无恢复”的渲染/输入）。
```

### 14.2 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| ConPTY 自研 spawn 的兼容性（信号/编码/尺寸） | P1 先 spike：与 `WinptyBackend` 对照跑 T2/T3/T5；不达关则整体停在 P1，不带病进 P2 |
| **权威仿真器候选（headless xterm sidecar）保真不足/不可用** | P1 spike 保真矩阵；不达关则快照 `fidelity=partial`、UI 明示、T17/T19 按降级断言验收；**不允许**静默完整恢复 |
| **Node sidecar 依赖引入**（runner 需 Node 运行时） | 启动自检 Node 与 addon 版本（pin），缺依赖时 runner 以 `fidelity=partial` 降级启动并在创建响应中上报；不阻断终端基本功能 |
| **DPAPI 同用户边界** | 威胁模型如实写进文档；同用户恶意进程不可防（§6.1 第 5 条）；其它用户由 ACL/DPAPI 隔离；T13/T20 覆盖身份与占名 |
| 管道 ctypes DACL 出错导致拒绝服务 | 测试覆盖“owner 可连/他人不可连”；失败时明确报错而不是降级为 loopback |
| xterm.js serialize 覆盖不足（鼠标/模式） | `fidelity` 上报 + 快照矩阵限定；不承诺未测模式 |
| 前端 hook/TS 校验阻断 | 前端改动集中在 TA-D；每步 `pnpm build` |
| PR6 测试私有断言冲突 | §10 分类适配；行为级零改动 |
| 慢客户端/背压回归 | T4 作为 P2 出口必测项 |
| detach 与 reconcile 语义错配 | T9–T11、T18、T20 固定验收；身份不匹配一律拒杀 |
| 快速重启产生幽灵 runner/错误状态 | §5.5 状态机 + pipe 独占 + T18；禁止把未确认关闭的终端标 `lost` |

### 14.3 待定项（不阻塞 P0–P1 起跑；其中第 2 项阻塞 P3 快照恢复验收）

1. ConPTY host/IO 迁移可行性（专门探针，未测，不阻塞首版）；
2. **权威仿真器引擎定版**：P1 spike 结论决定 `fidelity/recovery=full|partial`；未验证通过不得进入
   P3 的快照恢复验收（T17/T19 只能按降级语义验收）；
3. 快照滚动深度/保留窗口的最终值（默认 1000 行，前端联调可调）。

### 14.4 计划自查（写作时已核对的事实）

- **修订记录（v2，本次）**：按 MA 审查修正三处闭环缺口——①权威快照改为 runner 内常驻仿真器
  （浏览器非权威、browserless 可恢复、供给/降级/原子性与 detach 跨重启，§8 重写）；
  ②IPC 凭证改为 DPAPI 秘密文件 + pipe 独占 + 服务端身份核验（跨 Pan 重启可恢复、防冒充，§6.1 重写）；
  ③Web 授权补齐入口信任边界（WS Origin/REST CSRF/非 loopback 关闭/MCP 身份与 scope 非授权，§6.3 重写）。
  另：P5 转为必做最终验收；lease 统一死期 2s；shutdown/快速重启状态机（§5.5）；
  身份存活判定改为同 handle `WaitForSingleObject` signaled（§3.1/§5.4）。
- 引用文件存在性：brief/契约/生命周期/PR6 审查/PR6 源码树均在本文档头部列出的路径可读（只读访问）。
- PR6 测试基线：`tests/test_rewind_*.py` 7 个文件，审查实跑 **62 passed**（本计划 grep 计数 47 个
  `def test_` 函数名 + `test_rewind_scope.py` 3 处 parametrize 展开等，口径与 pytest 收集数不同；
  接入验收以“实跑 62”为基线，若并入后收集数变化，以不减少行为断言为准并在证据中记录差异）。
  `tests/test_terminal_broadcast.py`（8 项）属主线广播测试，另计。
- 接口锚点：`packages/web/server.py` WS 在 L4687/L4864、lifespan L187-274；
  `packages/mcp/server.py` 工具注册为 `@mcp.tool()`（如 `agent_background_start` L1720）；
  `packages/core/config.py::resolve_pan_python_argv` L335；session `workspace_ids` L1259；
  `packages/core/workspace.py` 存在；CI `test.yml` = windows-latest × py3.12/3.14；
  `minimal-requirements.txt` 当前含 psutil、无 pywinpty（PR6 的 pywinpty==3.0.5 只在 PR 分支）。
- 契约原型 API：`audit/terminal/contract/pty_contract.py` 公共类已核对（PtyBackend/OutputLog/PtyRuntime/
  TerminalRegistry/AttachmentRegistry/build_runtime 等），迁移时按 §3.1 去探针化。
- 存活判定口径差异：生命周期探针 `9bd858e2` 的 `kill_verified_detail` 用“退出码 != 259”判活；
  本计划生产实现改用**同一 handle 的 `WaitForSingleObject`**（`WAIT_TIMEOUT`=活/`WAIT_OBJECT_0`=死），
  因为 259 同时是 STILL_ACTIVE 与合法退出码；探针证据仍有效（FILETIME/终止语义不变）。

### 14.5 生命周期闭环自查（本次修订重点；逐条按 §2/§5/§6/§8 推演，无悬空分支）

| 闭环 | 场景链 | 结论 |
| --- | --- | --- |
| **Close** | 无浏览器也执行：`POST /close` → 服务校验入口（Origin）→ 秘密文件读取 → pipe 身份核验 → `stop` → runner 快照所有权→终止→残留核对→写终态 → 服务确认整树退出（5s）→ 删秘密 → 记录 `exited`；失败 → `cleanup_failed` 可重试，不谎报 | 闭环，不依赖浏览器/仿真器存活 |
| **Browserless** | 创建后全程无浏览器：reader 持续喂 OutputLog + 仿真器；>256 KiB 后窗口驱逐只影响游标；任意时刻新浏览器：WS → 身份/Origin 校验 → 取 runner 权威快照（cursor/rows/cols 原子）→ 从 cursor 接流；引擎未验证或 `feed_lag` → `recovery=partial/degraded` + 显式重打基线 | 闭环；**不再有“无仿真器消费/截断后无法恢复”的空洞** |
| **Detach** | `detach` → runner durable；Pan 退出（不 stop）→ 秘密保留、记录 `detached`；浏览器全关无影响；新 Pan（同用户）→ DPAPI 解密 → pipe 占名检查 + `GetNamedPipeServerProcessId` + FILETIME 身份核验 → re-attach → 取快照 → 同 PID/FILETIME 重连；伪造 pipe/错 token 拒绝 | 闭环；凭证跨 Pan 重启可恢复，冒充被身份核验挡住 |
| **Restart** | 正常关闭：`stopping` → 确认 `exited`（不标 lost）；快速重启：新 Pan 不新建同 id runner（pipe 独占），按 §5.5 收敛旧记录；崩溃重启：managed 等统一死期后复核 → `exited(lease_expired)`，仍存活→`cleanup_failed` 重试；detached → re-attach；机器重启 → 全部 `exited` 并清秘密 | 闭环；不伪标 lost、不漏进程 |
- 生命周期证据：`9bd858e2`（s1–s6 67/67、H0–H10 11/11、K1–K3 3/3）为 runnable 探针，可直接复用为
  P1 的门禁/身份/lease 行为参照（代码复制，不做运行时依赖）。

---

## 附录 A：与输入文档的对应关系

| 本计划条目 | 输入依据 |
| --- | --- |
| §1 范围 | brief；契约 `eb4d5589` 首版范围校准；审查 §建议边界 |
| §4 与 Pan Job 关系 | 审查 §与 Pan 后台 Job 的关系；生命周期报告 §6 |
| §5 布局与门禁 | 生命周期 `9bd858e2` §4.3/§5.1、s6；契约 §2.8/§2.9、M16/M17 |
| §6 IPC/认证 | 契约 §2.5/§7-5、M14.19；审查 §IPC 只允许当前本机用户 |
| §7 输出/EOF/背压 | 契约 §2.2/§2.3、R1/R4/R7/R9、M12/M13/M15；审查问题 1/2 |
| §8 快照 | 契约 C6/C7、R6、M10；审查 §输出需要序号与确认游标 |
| §10 PR6 接入 | 审查 §验证记录；契约 §3 映射表 |
| §11 验收矩阵 | 审查 §可执行验收场景（全覆盖并加隔离/门禁项） |
| §12 并行切分 | 本文档新增（MA 要求：拆分但不由本 TA 派发） |

## 附录 B：生产保障边界（防误用清单）

- `PsutilTreeTerminator` / psutil `children()`：只做测试观察；生产整树所有权 = `JobObjectGuard`。
- `JobObjectTreeTerminator`（契约原型）：`NotImplementedError` 占位；生产实现是 §3.1 `guard.py`。
- `PyteScreenObserver` / `PyteEmulator`：`fidelity="partial"`；只服务自动化或显式降级；
  网页恢复由 runner 内**常驻权威仿真器**承担（候选 headless xterm，须验证）；**浏览器不是权威**。
- 生命周期探针的 loopback + 明文 token：**不是**安全模型；生产走 owner-only 管道 DACL + DPAPI 秘密存储 + token。
- “声明式 gate 字段已填”不等于门禁：必须携带来自真实 spawn 的证据（四要素 + 原子性）。
- 任何“自动重连/自动恢复/完整恢复”不得绕过身份核验、lease 与 `fidelity/recovery` 声明；
  不可核验 = 不触碰进程；不可完整恢复 = 明确降级，不静默伪装。
