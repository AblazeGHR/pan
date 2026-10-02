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
| 输出通道 | 有界输出日志（绝对字节偏移）、gap 返回、浏览器重连/刷新恢复；慢客户端断开并从快照恢复 |
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
- 权威快照的“全保真”承诺：首版快照 = xterm.js serialize（主屏/备用屏/滚动/模式随 addon 能力，
  `fidelity` 字段如实上报），不承诺覆盖所有终端模式。

---

## 2. 目标架构

```
┌─ 浏览器（React + xterm.js/fit/serialize） ─────────────────────────────┐
│  TerminalPanel: 订阅 / 输入 / resize / ack / 快照恢复                   │
└───────────────▲────────────────────────────────────────────────────────┘
                │ WS /ws/terminal/{term_id}（JSON 消息；Pan 入口授权）
┌───────────────┴────────────────────── Pan 服务进程 ────────────────────┐
│  packages/core/terminal/service.py  TerminalService                    │
│   ├─ TerminalRegistry（data/terminals/<id>.json，原子写+命名互斥）      │
│   ├─ AttachmentRegistry（control lease / generation / 撤销）           │
│   ├─ runner 管理器：派生日志、身份核验、reconcile                       │
│   └─ WS 桥：游标分发、ack、gap→快照、exit 事件                          │
│  lifespan: startup reconcile；shutdown 停非 detached、保留 detached     │
└───────────────▲────────────────────────────────────────────────────────┘
                │ IPC：\\.\pipe\pan-terminal-<term_id>
                │ （Named Pipe，owner-only DACL + 256bit token；
                │   仅 Pan 服务与 runner 两端；浏览器永不接触）
┌───────────────┴──────────── 每终端一个 runner 进程（detached）──────────┐
│  python -m packages.core.terminal.runner --terminal-id <id>            │
│   ├─ 出生持有 PTY（ConPTY，suspended spawn→assign→resume）             │
│   ├─ 自持 Job Object（KILL_ON_JOB_CLOSE）罩住 PTY 整树                 │
│   ├─ OutputLog（有界 256 KiB）+ reader drain（EOF 才结束）             │
│   ├─ lease：Pan 服务持有；丢失且未 detach → grace 后自停               │
│   ├─ detach 置位 → durable；显式 stop → 快照所有权→终止→残留核对       │
│   └─ 控制端点：命名管道 JSON-line（input/read/resize/lease/stop…）     │
└─────────────────────────────────────────────────────────────────────────┘
```

数据流要点：

- runner 是**唯一的 PTY 属主**（布局 B：出生即拥有，不进任何服务级 kill-on-close Job；
  guard 句柄只归 runner 进程）。Pan 服务只持 lease 与控制连接，崩溃/重启都不改变 runner 树。
- Pan 服务不代理逐字节输出（输出在 runner 的有界日志里）；WS 桥按游标拉取/推送，天然支持重连。
- `TerminalRecord` 只存事实与提示（pid/创建时间/pipe 名/detached/scope/exit 事实），不存 token。

---

## 3. 模块与文件边界（新增/修改清单）

### 3.1 公共核心：新增 `packages/core/terminal/`（从契约原型迁移，去探针化）

| 文件 | 职责 | 备注 |
| --- | --- | --- |
| `__init__.py` | 导出公共名（`PtyBackend/OutputLog/PtyRuntime/TerminalRegistry/build_runtime/...`） | 无副作用导入 |
| `backend.py` | `PtyBackend` Protocol；`ConPtyBackend`（Windows 生产）；`WinptyBackend`（参照/回归对照，**不进生产门禁路径**）；`BackendUnavailableError` | 延迟导入 Windows 依赖 |
| `spawn_win.py` | `spawn_conpty_suspended(argv, cwd, env, rows, cols)`：`CreatePseudoConsole` + `InitializeProcThreadAttributeList` + `UpdateProcThreadAttribute(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE)` + `CreateProcessW(CREATE_SUSPENDED|EXTENDED_STARTUPINFO_PRESENT)` + `AssignProcessToJobObject` + `ResumeThread`；返回 `SpawnEvidence{pid, createTimeFiletime, assigned, atomic_with_spawn, guard}` | P0 先做 spike + 证据，再进 core |
| `guard.py` | `TreeGuard` Protocol；`JobObjectGuard`（生产：创建/assign/terminate/active/close/is_member）；文件头注明：psutil 方案仅测试观察，非生产 | 句柄由 runner 生命周期持有 |
| `identity.py` | `ProcessIdentity`（pid + 100ns FILETIME + 退出码）；`kill_verified` 单句柄原子核验终止；`is_running` | 移植生命周期探针 `9bd858e2` 的 `kill_verified_detail` 语义 |
| `output.py` | `OutputLog/OutputChunk/OutputPage/InvalidCursorError`（绝对偏移、整块驱逐、gap、无补零） | 契约 §2.2 |
| `runtime.py` | `PtyRuntime`、`RuntimeState`、`ExitInfo`、`CleanupReport`、`IllegalStateTransition`；drain 循环（EOF 才结束、`eof_grace`）；`close()` 顺序=快照所有权→可选中断→终止→残留核对→drain 收敛→join reader→关句柄 | 契约 §2.3 |
| `ownership.py` | `OwnershipPolicy/OwnershipMode`、`StartupOwnershipGate` 四要素、`build_runtime` fail-closed 工厂 | 布局中立，不按 `lifecycle_owner` 分支 |
| `registry.py` | `TerminalRecord/TerminalRegistry/UnknownTerminalError`；持久化、原子写、命名互斥（复制 `background_jobs` 模式，独立实现，**不改** `background_jobs.py`） | 目录 `data/terminals/` |
| `attachments.py` | `LeaseToken/AttachmentRegistry/StaleLeaseError/NotControlLeaseError`；撤销不可复活、同临界区校验+写 | 契约 §2.5 |
| `observer.py` | `ScreenObserver` Protocol、`ScreenSnapshot`、`PyteScreenObserver(fidelity="partial")` | 自动化观察专用 |
| `driver.py` | `AutomationContext`、`AutomationDriver`（`send_text/send_keys/screen_text/wait_for(quiet_ms)`） | 公共核心**零** CBC 字面量 |
| `ipc.py` | 帧协议（JSON-line）、消息类型常量、token 校验（`hmac.compare_digest`）、日志脱敏助手 | 与 `win_pipe.py` 解耦 |
| `win_pipe.py` | 命名管道 server/client（ctypes：`CreateNamedPipeW` + owner-only DACL：`GetTokenInformation(TokenUser)`→`SetEntriesInAclW`→`InitializeSecurityDescriptor/SetSecurityDescriptorDacl`） | P1 spike 先行；非 Windows 抛 `BackendUnavailableError` |
| `runner.py` | runner 进程主逻辑（`python -m packages.core.terminal.runner`）：持 PTY+guard、pipe 控制端点、lease 监控、detach、stop、崩溃安全清理 | 独立进程入口 |
| `service.py` | `TerminalService`：create/list/get/close/detach/snapshot/input/resize；runner 派生（`resolve_pan_python_argv` + `DETACHED_PROCESS`）；reconcile；WS 会话桥；lifespan 钩子 | Pan 进程内单例 |

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
| `tests/test_terminal_api_ws.py` | TestClient + mock service | WS 协议、ack、gap→快照、无权连接拒绝 |
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
| 浏览器断开/刷新 | 无变化（仅释放 WS） | 无变化 |
| Pan 正常关闭 | 服务发 `stop`；runner 快照所有权→终止→残留核对→写终态 | 服务 `EXIT_KEEP` 语义：不 stop，仅撤销 lease 并落盘 `detached` 状态 |
| Pan 崩溃 | lease 丢失，grace（默认 2s）后 runner 自停并整树清理 | 忽略 lease 丢失，继续运行 |
| 显式关闭终端 | 同上 `stop` 路径（杀整树） | 同左（显式操作） |
| runner 崩溃/被硬杀 | guard 句柄由内核关闭 → 整树死 | 同左（树死；registry 记录转 `exited`/`lost`，reconcile 不伪造存活） |
| Pan 重启 | registry 中标 `lost`（服务崩溃后无法恢复非 detach 终端） | reconcile：身份核验（pid+FILETIME）后 re-attach lease/控制；不可核验→标 `lost`，**不杀进程** |
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
reconcile 只信 `pid + FILETIME`（单句柄核验），不认识/不匹配一律 `lost`；绝不按 PID 单值杀进程。

---

## 6. IPC 身份认证与 token 不泄露

### 6.1 传输与认证（首版目标）

- 传输：Windows 命名管道 `\\.\pipe\pan-terminal-<term_id>`；DACL 仅当前用户（owner SID）——
  第一道防线；token 第二道（防同用户其它程序冒名 + lease 撤销身份语义）。
- token：服务生成 `secrets.token_hex(32)`，随创建请求经标准输入/pipe 交给 runner（runner 启动参数
  **不**携带 token），runner 侧 `hmac.compare_digest` 校验；撤销/世代语义见 `attachments.py`。
- 非 Windows/开发降级：loopback TCP + token（仅原型/测试），生产 Windows 路径必须走 pipe。

### 6.2 不泄露规则（实现硬约束 + 测试断言）

1. token **不得**出现在：日志、`data/terminals/*.json`、错误消息、HTTP/WS 响应体、URL query、
   异常回溯字符串；日志仅打 `terminal_id` 与 `client_id`。
2. 浏览器**永不**接触 runner token：前端只用 `terminal_id` + Pan 会话；服务端 WS 桥持有双向连接。
3. runner 崩溃日志同样脱敏（runner 启动参数不含 token；token 走受控通道）。
4. 测试断言：`test_terminal_runner_ipc.py` 扫描 runner/service 日志文件与 registry JSON 不出现 token
   （用创建时注入的哨兵值反查）。
5. 已知边界（如实声明）：token 内容等价即可通过（契约 M14.19）——因此授权必须由 Pan 入口先完成，
   registry 只做进程内单 writer/撤销；管道 ACL 是同类风险的兜底。

### 6.3 浏览器/入口授权

沿用 Pan 现有入口（本地 loopback、无鉴权现状 + LAN 暴露警告）；终端连接继承此边界，
不做“知道 terminal_id 即可操作”：所有 API/WS 都经过 `TerminalService`，由服务校验收紧。

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
  即返回 `gap=(cursor, first_retained_seq)`，由客户端走快照恢复。
- WS 桥推送带 `nextSeq`；浏览器在渲染完成后上报 `ack` 游标；服务端对每连接设有界发送队列
  （默认 4 MiB），超限断开该连接（其它连接与 PTY 不受影响）。
- PTY reader 永不因客户端阻塞：它只追加到有界日志。

### 7.3 gap 与 resize 同步

- gap 语义（契约 R4/R7、M15）：不补零；窗口起点可能落在 UTF-8/CSI/OSC 中间——
  客户端**不得**从窗口起点解析，必须快照恢复。
- resize：仅 control lease 可通过 `PtyRuntime.resize(rows, cols)` 修改；同时动作到
  ① ConPTY（`ResizePseudoConsole`）② observer ③ 客户端 fit addon；旧 generation 的 resize 被拒。
  尺寸跟随当前控制客户端；`term.snapshot` 带尺寸以便新观察者对齐。

---

## 8. 快照与 gap 权威恢复

- 权威快照引擎：**xterm.js + serialize addon**（浏览器侧），快照含主/备屏与模式（由实际 addon 能力决定，
  `fidelity` 字段如实上报）；这正是契约 C6/C7 结论（pyte 不能当权威）的实现侧落点。
- `PyteScreenObserver` 仅用于无浏览器场景的自动化观察（PR6 回滚 driver、自动化测试），
  报告/接口中 `fidelity="partial"` 必须显式暴露，不得宣称网页 TUI 状态恢复。
- 重连流程（浏览器刷新/断线）：
  `ws connect → 请求 head 快照 → 渲染 → 携带 `cursor=nextSeq` 继续接收 → 若收 gap → 重新快照并显式丢弃旧屏`。
- 首版不承诺“任意 TUI 全保真恢复”；矩阵按 xterm.js serialize 实际覆盖的模式验收（§11 T3/T7）。

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
| T7 | 刷新重连 | 同 terminal_id 新 WS | 快照恢复屏幕与模式；无重复输入（lease 校验 + ack 游标） |
| T8 | 显式关闭 | `POST /close` | 整树退出（含 2 层孙进程）；失败保留可重试状态 |
| T9 | detach + Pan 正常退出 | detach 后优雅退出 Pan | 同 PID/FILETIME 存活；新 Pan/客户端用 reconnect_hint 重连成功 |
| T10 | 默认 + Pan 崩溃 | 杀 Pan 进程（隔离实例） | grace 后 runner 自停、整树清理；detach 的终端不受影响 |
| T11 | Pan 重启 reconcile | 重启隔离 Pan | managed 标 `lost`；detached 核验身份后 re-attach；不杀不可核验进程 |
| T12 | runner 硬杀 | 身份核验后终止 runner | guard 句柄关闭→整树 0.6s 级清理；registry 转终态 |
| T13 | 未授权/错误 token | 直接连 pipe（另一用户或错 token） | 拒绝且无任何输入生效；日志无 token |
| T14 | assign 失败门禁 | 注入 assign 失败 | 不发布 running/不开端点；仅清理自有后代；API 返回明确错误 |
| T15 | 迟到输入/resize | 控制权转交后旧客户端重放 | `StaleLeaseError` 拒绝，终端状态不变 |
| T16 | PR6 回滚回归 | 集成树 | 62 项通过 + 回滚 E2E 通过 |

---

## 12. 阶段划分与并行 TA 职责（未来派发；本计划不派任何子代理）

### 12.1 阶段与出口标准

| 阶段 | 内容 | 出口标准 |
| --- | --- | --- |
| **P0 契约落地（无 UI/无服务接线）** | `output/runtime/registry/attachments/observer/driver/ownership` + ScriptedBackend 测试 | `tests/test_terminal_output|runtime|registry|lease` 全绿；接口冻结 |
| **P1 Windows 生产 backend** | `spawn_win` spike（先证据）→ `ConPtyBackend`；`guard/identity/win_pipe/ipc`；`runner.py` 进程闭环；门禁四要素 | `test_terminal_spawn_gate/runner_ipc` 全绿；原子 spawn 证据；s6 语义移植证据 |
| **P2 服务层与 API** | `service.py`、REST/WS、MCP、lifespan 接线、reconcile | T1–T2、T5–T6、T9–T14 通过（隔离实例） |
| **P3 前端** | xterm.js 面板、重连/gap/ack、快照 | T3、T4、T7 通过；`pnpm build` 通过；hook TS 检查通过 |
| **P4 PR6 接入** | `_PtySession` 替换、菜单迁 `automation.py`、62 测试 | §10 门槛全过 |
| **P5 收尾加固（可选）** | 管道 DACL 压测、lease 抖动、reconcile 边界、文档 | T15–T16 全绿；`docs/USER_MANUAL` 增补（如 MA 要求） |

### 12.2 并行 TA 切分（无相互阻塞的界面）

| TA | 职责 | 依赖 | 写文件范围（互斥） |
| --- | --- | --- | --- |
| TA-A 核心逻辑 | `output/runtime/registry/attachments/observer/driver` + 纯逻辑测试 | 契约原型（只读） | `packages/core/terminal/{output,runtime,registry,attachments,observer,driver}.py`、`tests/test_terminal_{output,runtime,registry,lease}.py` |
| TA-B Windows backend | `spawn_win/backend/guard/identity/win_pipe/ipc/runner` + 门禁测试 | A 的接口签名冻结 | `packages/core/terminal/{spawn_win,backend,guard,identity,win_pipe,ipc,runner}.py`、`tests/test_terminal_{spawn_gate,runner_ipc}.py` |
| TA-C 服务与 API | `service.py`、`server.py`、`mcp/server.py`、lifespan、隔离 E2E | A+B（可用 mock runtime 先行） | `packages/core/terminal/service.py`、`packages/web/server.py`（终端段落）、`packages/mcp/server.py`（终端工具）、`tests/test_terminal_{service,api_ws,e2e}.py` |
| TA-D 前端 | 面板/WS 客户端/快照/ack | WS 协议冻结（C 提供 mock） | `packages/web/src/**`、`packages/web/package.json` |
| TA-E PR6 接入 | rewind 改造与测试接入 | A+C；PR6 已在集成树 | `packages/core/rewind/**`、`tests/test_rewind_*.py` |

并行规则：接口先冻结（本文档 §2/§3 即冻结基线，变更走记录）；共享文件一次只允许一个 TA 写；
每阶段结束由 MA 在集成树做一次合并与验收，验收命令固定（§11）。

---

## 13. 工程默认参数（建议默认，实施可微调；无需再问用户）

| 参数 | 默认 |
| --- | --- |
| `read_size` | 64 KiB |
| `OutputLog.max_bytes` | 256 KiB/终端（对齐契约实测上限 262144） |
| `eof_grace` | 8 s |
| lease grace | 2 s（生命周期实测）；心跳 1 s、连续 3 次丢失判定 |
| WS 每连接发送队列 | 4 MiB，超限断开 |
| 快照 | xterm.js serialize；滚动深度默认 1000 行；随 addon 能力如实上报 |
| token | `secrets.token_hex(32)`；`compare_digest`；rotate=每次 detach/re-attach 可选（默认不 rotate） |
| 管道名 | `\\.\pipe\pan-terminal-<term_id>`；DACL=owner SID |
| registry | `data/terminals/`（`PAN_TERMINALS_DIR` 可覆盖）；原子写+命名互斥 |
| 默认 shell | `cmd.exe /q /d`；`terminal.shell` 配置可换 `pwsh.exe` |
| 终端数量软上限 | 8（超出返回明确错误） |
| 清理顺序 | 快照所有权→可选中断→终止→残留核对→drain 收敛→join reader→关句柄 |

---

## 14. 依赖、风险与自查

### 14.1 依赖顺序（硬约束）

```
P0(A) ──► P1(B) ──► P2(C) ──► P3(D)
                     └──────► P4(E)（PR6 并入集成树后）
A 接口冻结是 B/C/D 的前置；B 的原子 spawn 证据是任何 “running 终端” 验收的前置。
```

### 14.2 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| ConPTY 自研 spawn 的兼容性（信号/编码/尺寸） | P1 先 spike：与 `WinptyBackend` 对照跑 T2/T3/T5；不达关则整体停在 P1，不带病进 P2 |
| 管道 ctypes DACL 出错导致拒绝服务 | 测试覆盖“owner 可连/他人不可连”；失败时明确报错而不是降级为 loopback |
| xterm.js serialize 覆盖不足（鼠标/模式） | `fidelity` 上报 + 快照矩阵限定；不承诺未测模式 |
| 前端 hook/TS 校验阻断 | 前端改动集中在 TA-D；每步 `pnpm build` |
| PR6 测试私有断言冲突 | §10 分类适配；行为级零改动 |
| 慢客户端/背压回归 | T4 作为 P2 出口必测项 |
| detach 与 reconcile 语义错配 | T9–T11 固定验收；身份不匹配一律拒杀 |

### 14.3 待 MA 归档的待定项（不影响 P0–P4 起跑）

1. ConPTY host/IO 迁移可行性（专门探针，未测，不阻塞首版）；
2. gap 恢复的产品语义（自动重放 vs 强制重连；本计划默认“强制快照恢复”，如需自动重放再定）；
3. 快照滚动深度/保留窗口的最终值（默认已给，前端联调时可调）。

### 14.4 计划自查（写作时已核对的事实）

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
- `PyteScreenObserver`：`fidelity="partial"`；只服务自动化；网页恢复由 xterm.js serialize 承担。
- 生命周期探针的 loopback + 明文 token：**不是**安全模型；生产走管道 DACL + token。
- “声明式 gate 字段已填”不等于门禁：必须携带来自真实 spawn 的证据（四要素 + 原子性）。
- 任何“自动重连/自动恢复”不得绕过身份核验与 lease；不可核验 = 不触碰进程。
