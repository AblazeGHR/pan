# Pan Terminal / PTY 公共契约与 PR #6 接入设计

- 业务任务：T-TERMINAL-PTY-20261003（公共 PTY 契约与 PR #6 接入，effort high）
- 日期：2026-10-03
- 工作树：`D:/project/pan-worktrees/terminal-contract-explore-20261003`
- 分支：`explore/terminal-contract-20261003`，起点 `35b6fb1abaae9d741b3042470aa32a8c356e431d`
- PR 只读源码树：`D:/project/pan-worktrees/pr6-terminal-source-20261002`（`387a43ec4dfe699498bb0b95d7ceef212f905b99`）
- 交付物：`audit/terminal/contract/`（契约原型 + 可运行探针 + JSON 证据）、本文档
- 性质：**探索阶段交付**。未修改 `packages/` 下任何正式模块，未改正式依赖锁，未合入/推送，未重启任何服务。
- 版本：首轮 `e70543da`；本文件为 MA 追加修正（结束原因分类 / reader 回收 / lease 撤销 / 输出边界 / 所有权工厂）后的 follow-up 版本。

---

## 0. 结论速览

| # | 结论 | 证据类型 |
|---|---|---|
| C1 | PR `_read()` 先判 `isalive()` 再读，在真实 ConPTY 上会丢尾部输出：进程先退出后才开始读时，PR 控制流捕获 **0 字节**，之后仍能读到 **65 字节**（含输出标记），2/2 次复现 | 实测（R1.B1/B2/B3） |
| C2 | 持续读取时该竞态是时序相关的：2/2 次未命中（PR 控制流也读全了）。所以契约要求"读到通道 EOF"，而不是靠时序碰对 | 实测（R1.B2.x，如实记录） |
| C3 | 输出必须有界：真实 1,240,033 字节输出、客户端全程不读，保留量稳定在 **262,136 ≤ 262,144** 上限，PTY/进程未被拖死，退出码正确 | 实测（R4） |
| C4 | 序号用**绝对字节偏移**后，落后游标可返回明确 gap：`gap=(0, 977897)`，gap 之后可继续追平且不重复 | 实测（R4.6/R7.1） |
| C5 | resize 能真正传到子控制台：`mode con` 从 (24,80) 变为 (40,132)；控制权转交后旧客户端的 resize 被 generation 拒绝且对真实终端无影响 | 实测（R3.1–R3.4） |
| C6 | **pyte 0.8.2 不能承担 TUI 状态恢复**：无备用屏缓冲（全源码无 47/1047/1049 处理）、无滚动历史、私有模式存为 `mode<<5`。真实 `less` TUI 中快照只能给"当前单缓冲文本 + 模式位"；退出备用屏后 pyte 屏幕上仍是 TUI 残留（ConPTY 未重发主屏内容） | 实测（R6.4–R6.17）+ 源码 |
| C7 | 流中间加入的客户端（等价于重连/迟到观察者）**无法从后续字节推断模式状态**：`mid_stream_alt_screen=False`，而当时真实状态是 `True`；尾部文本通道结构上无法承载状态 | 实测（R6.8/R6.15/R6.17）+ 确定性测试（M10） |
| C8 | 清理失败可保留 owner：注入终止失败后状态为 `cleanup-failed`、`owner_retained=true`、后端句柄未关闭、**真实进程仍然存活**（未谎报退出），重试后才进入 `exited` 且进程消失 | 实测（R5b，注入故障已标注） |
| C9 | 所有权快照必须先于终止：`PsutilTreeTerminator.owned_pids` 在终止后才调用会返回空集（根 PID 已消失）。契约改为"先快照所有权→再终止→再核对残留"，报告中出现 `owned=[49612, 6548]` | 实测（R5.5）+ PR 源码（`driver.py:558-582`） |
| C10 | terminal 命名空间与 session/worker 分离、状态机合法性等契约逻辑全部通过（首轮 M1–M11 73 项；追加修正后 M1–M17 共 145 项，0 失败） | 针对性测试（M1–M17） |
| C11 | **结束原因必须分类**：真实 EOF 才允许 `channel_eof`/`output_complete`；通道错误、`eof_grace` 超时、stop 请求一律不声明输出完整。实测 pywinpty 的"关闭句柄取消读"表现为 `ConnectionAbortedError`（WinError 10053），归因为独立的 `cancelled`（既不是通道错误也不是对端 EOF） | 实测（R9.4b）+ 确定性测试（M12） |
| C12 | **reader 回收必须被证明**：正常路径 `terminate` 会释放阻塞读（自然收敛，`cancel_kind=none`）；需要取消时先等满 grace 再用关句柄取消并证明 `reader_converged`。取消无效则拒绝 `exited`、保留 owner（不能用 daemon 线程当回收保证） | 实测（R9.2a/R9.2b/R9.3b）+ 确定性测试（M13） |
| C13 | **lease 撤销不可复活**：撤销后 send/resize/transfer 全部 `StaleLeaseError`，200 次 churn 无撤销后写入；伪造 `revocation_id` 的 token 被 `NotControlLeaseError` 拒绝；校验与写入在同一临界区 | 确定性测试（M14.4–M14.18） |
| C14 | **清理身份核验可拒杀**：注入"身份不匹配"时清理拒绝终止（`refused-identity-mismatch`、`identity_check=mismatch`、owner 保留），**真实进程仍然存活**；换回真实身份重试才收敛且进程消失。FILETIME 精确比较，无可比字段视为不匹配 | 实测（R8.4–R8.9）+ 确定性测试（M17） |
| C15 | **所有权布局中立 + fail-closed 工厂**：`lifecycle_owner` 仅声明性数据，`pan-service`/`runner`/`external-host` 三者行为一致；无策略/声明守卫却给 None/detach/external 四种情形全部被拒；`assigned`/`atomic_with_spawn`/`identity`/`handle_bound` 四要素缺一即拒绝进入 `running`（状态保持 `created`） | 确定性测试（M16.1–M16.11）+ 真实探针经工厂构造（R0） |
| C16 | **输出边界是字节流不是序列流**（纠正先前过强表述）：跨块 UTF-8/CSI/OSC 会被切割；保留窗口起点可落在 `[38;5;196m` 内部并返回 gap。序列重组归仿真器/快照负责 | 确定性测试（M15.1–M15.7） |

---

## 1. 起点核对与技术环境

核对命令与结果（本工作树内执行）：

```
$ git rev-parse --show-toplevel   -> D:/project/pan-worktrees/terminal-contract-explore-20261003
$ git rev-parse --abbrev-ref HEAD -> explore/terminal-contract-20261003
$ git rev-parse HEAD              -> 35b6fb1abaae9d741b3042470aa32a8c356e431d
$ git status --porcelain=v1       -> （空，开始时干净）
```

- 本工作树**没有** `AGENTS.md`、`CLAUDE.md` 或 `.cursorrules`（`git ls-files` 零命中）；适用的项目约定只有
  `CODEBUDDY.md`（Pan 开发约定：React-only 前端、`git config core.hooksPath scripts`、`python -m pytest tests/ -q`）。
- `git worktree list` 确认同批 4 个探索树（cbc / codex / lifecycle / contract）都停在 `35b6fb1a`；集成树为
  `D:/project/pan-worktrees/pr6-terminal-20261002`（`audit/pr6-terminal-20261002`）。本报告未触碰其中任何一个。

运行环境：

| 项 | 值 |
|---|---|
| OS | Windows 11 `10.0.26200.9457`；`platform.platform()` = `Windows-11-10.0.26200-SP0`（MINGW64 bash） |
| Python | 3.12.12（`E:/software/miniforge/python.exe`） |
| uv | 0.9.14 |
| PTY 依赖 | `pywinpty==3.0.5`（PR `minimal-requirements.txt` 固定值）、`pyte==0.8.2`、`psutil 7.2.2` |
| git | 2.51.2.windows.1 |

依赖隔离：全部用 `uv run --no-project --with ...` 的临时环境运行，**未向全局环境安装任何包，未修改
`requirements.txt` / `minimal-requirements.txt` / 任何锁文件**。

隔离与安全：

- 未打开任何监听端口（`network_ports_opened: []`）；`ping -n 60 127.0.0.1` 只走 ICMP 回环，由本探针启动并清理。
- 只在 `%TEMP%\pan_pty_contract_probe_*` 写测试文件，运行结束已删除（C2 通过）。
- 未操作任何既有 Session / Worker / CLI thread / 服务；未读写任何 secret。
- 所有自建进程结束后核对不再存活（C1 通过，`leftover: []`）。

---

## 2. 最小接口与状态机

原型实现：`audit/terminal/contract/pty_contract.py`（约 700 行，含文档与断言前提）。以下为契约核心。

### 2.1 `PtyBackend`：平台原语（6 个方法）

```python
class PtyBackend(Protocol):
    pid: int | None
    def read(self, size: int) -> bytes: ...   # 阻塞；通道结束抛 EOFError
    def write(self, data: bytes) -> int: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def alive(self) -> bool: ...
    def exit_code(self) -> int | None: ...     # 未退出返回 None
    def terminate(self, force: bool) -> None: ...
    def close(self) -> None: ...
```

- `WinptyBackend` 是 **pywinpty/ConPTY 的薄适配**；依赖延迟导入，缺失时抛 `BackendUnavailableError` 并给出可执行的安装提示
  —— 对应审查"跨平台：`_PtySession` 无条件 import winpty"的问题，使 Windows 依赖不阻断其它平台安装。
- 实测补充（写进契约）：`PtyProcess.read()` **会无限阻塞**（无读超时），因此任何实现都必须有独立 reader 线程；
  `PtyProcess.eof` 是**方法**不是属性；`exitstatus` 在退出后为退出码。

### 2.2 `OutputLog`：有界、带序号、可判 gap

| 语义 | 定义 |
|---|---|
| 序号 `seq` | 该字节在**流中的绝对偏移**，与保留窗口无关，跨重连可复用 |
| 保留上界 | `retained_bytes <= max_bytes`（断言级） |
| 驱逐 | 以**整块**为单位，只避免"因驱逐在保留窗口**起点**额外制造残片"；**不保证** VT 完整性，单块大于上界时保留尾部并计入 gap |
| `read_from(cursor, max_bytes=None) -> OutputPage` | 返回 `chunks / first_seq / next_cursor / gap / truncated` |
| gap | `cursor < first_retained_seq` 时返回 `(cursor, first_retained_seq)`；**不补零**，要求客户端走快照恢复 |
| 非法游标 | `cursor > total` 抛 `InvalidCursorError` |

**边界是字节流，不是序列流（纠正先前过强表述）**：以下三种情况都会让 UTF-8 码点、CSI、OSC
跨块切割，日志层无法也不应消除：① `read(size)` 的读取边界由后端/内核决定（ConPTY 一次写入
可能分成多个读事件）；② 单块超上界时只保留尾部；③ 保留窗口起点 `first_retained_seq` 可能落在
一条转义序列内部。实测（M15）：`logger` 容量 8 时窗口起点落在 `[38;5;196m` 内部
（`window_start=9 > csi_start=5`）并返回 gap。因此契约要求 **序列重组与屏幕状态由仿真器/快照
负责**；客户端遇到 gap 或窗口起点可疑时必须走快照恢复，而不是从窗口起点解析。

策略选择（需 MA 确认产品语义）：**单条有界历史 + 快照恢复**，而**不是**每客户端独立缓冲。理由：慢客户端若各自排队
就是 PR 的无界队列问题换了个位置；契约要求慢到丢窗口的客户端断开并从快照恢复。浏览器确认游标应在其渲染完成后上报。

### 2.3 `PtyRuntime`：所有权 + drain + 三个退出事实

状态机（`_LEGAL_TRANSITIONS` 强制，非法跃迁抛 `IllegalStateTransition`）：

```
CREATED ──> STARTING ──> RUNNING ──> EXITING ──> EXITED
   │                        │            └─────> CLEANUP_FAILED ──> EXITING（重试）
   └──> EXITING             └──> LOST（通道错误/无法收敛）
```

**不同事实分开公布**（对应审查 §2 的"区分根进程退出、通道关闭和终端完成"；MA 追加修正：
不得把错误/超时/stop 当作正常 EOF）：

| 字段 | 含义 | 取值规则 |
|---|---|---|
| `process_exit_seen` + `code` | 根进程已退出、退出码 | `exit_code()` |
| `reader_done` | reader 线程结束 | **任何**结束原因都置位（含错误/取消） |
| `channel_eof` | 读到**对端**真实 EOF | 仅 `drain_stop_reason == "eof"` |
| `output_complete` | 输出完整 | **仅** `drain_stop_reason == "eof"` |
| `drain_stop_reason` | 结束原因分类 | `eof` / `cancelled` / `channel-error` / `eof-timeout` / `stop-requested` |

`close()` 报告另有 `reader_converged` / `reader_joined` / `reader_cancelled` / `cancel_kind`：
只有 reader 被证明回收（自然结束或取消后结束）才允许进入 `exited`。

实测时长差异（重要）：进程退出到通道 EOF 之间可以有**约 5 秒**延迟（scratch 测量：`isalive()` 在 3.08s 变假，
`EOFError` 在 8.03s 到达，其间仍读到 19 字节尾部）。所以**不能**用一个"完成"事件把三者混在一起。

reader 契约（`_drain_loop`）：循环 `read()`，只有 `EOFError`（或通道错误）才结束；`alive()` 为假**不**是退出条件。
空读时若进程已退出，进入有界 `eof_grace`（默认 8s）等待，避免永久卡死。

`close()` 契约顺序：**先快照所有权 → 可选中断 → 终止 → 核对残留 → 等 drain 收敛 → join reader → 关句柄**。
任何一步失败：进入 `CLEANUP_FAILED`、`owner_retained=True`、**不关闭后端句柄**、registry 保留记录，允许重试；
只有全部成功才进入 `EXITED` 并允许 `remove()`。

### 2.4 `TerminalRegistry`：全局终端对象

- ID 前缀 `term_`，与 Agent Session ID / Worker ID / Job ID 命名空间分离；可选关联 `workspace_id` / `session_id`。
- `register / get / list / close / remove`；`remove` 仅允许 `EXITED`/`LOST`。
- 生命周期所有权标记 `owner ∈ {service, detached}`，**默认 managed**（随 Pan 服务生死）。

### 2.5 attachment / control lease

- 一个终端多个观察连接，同一时刻**只有一个输入控制权**；`LeaseToken(terminal_id, client_id, generation, role)`
- `role="control"` 才可 `send` / `resize`；`observer` 写入或 resize 抛 `NotControlLeaseError`
- 控制权转交 `generation += 1`；旧 token 的 `send`/`resize` 抛 `StaleLeaseError`（防网络中迟到消息）
- **尺寸跟随当前控制客户端**；resize 必须同时作用于 PTY 与客户端仿真器（见 R6.11）
- 未授权/不存在 terminal id 直接失败；契约层面不接受"知道 ID 即可操作"

**MA 追加修正后的撤销语义（M14 实测）**：

- `detach(token)` 把该 token 的 `revocation_id` 记入 revoked 集合；控制权撤销时**同时自增
  generation**。此后该 token 与"同一代副本"的 `send`/`resize`/`transfer` 全部 `StaleLeaseError`，
  不再存在任何 `setdefault` 式复活路径（旧实现会复活，已删除）。
- `revocation_id` 由 registry 生成的不透明随机串承担"同一控制权"的判定；`role`/`client_id`
  是自报字段，伪造 token 即使自报 `role="control"`、世代正确，也会因
  `holder.revocation_id != token.revocation_id` 被 `NotControlLeaseError` 拒绝。
- **校验与操作在同一临界区**：`attach`/`transfer`/`detach`/`send`/`resize` 共用一个可重入锁，
  "校验 → 写终端"原子，单 writer 顺序有保证。200 次"取控制权→写→撤销→再写"churn 无一次
  撤销后写入（M14.18）。
- 观察者 token 的失败原因稳定为"无权限"（先判权限类别再判世代），观察者撤销不影响其它观察者
  （M14.16）；control 撤销**不会**误伤观察者（其有效性只由是否被撤销决定）。
- **明确未实现范围**：真实身份认证与网络授权（把 token 绑定到 Pan 登录用户/连接/ACL）、跨进程
  token 保密。已知边界：**复制 `revocation_id` 的 token 会被接受**（M14.19 如实记录为测量值），
  所以授权必须由 Pan 现有入口在先完成，本 registry 只解决进程内单 writer + 撤销语义。

### 2.6 `ScreenObserver`：自动化观察者

```python
def feed(data: bytes) -> None
def snapshot() -> ScreenSnapshot   # rows/cols/lines/cursor/alternate_screen/private_modes/
                                   # raw_mode_bits/saved_cursor/scrollback_lines/engine/fidelity/note
def wait_for(predicate, timeout, *, quiet_ms=0.0) -> WaitOutcome  # matched/timed_out/screen/last_text/observed_bytes
```

- `fidelity` **必须**显式声明。`PyteScreenObserver.fidelity="partial"`，并公开
  `SUPPORTS_ALTERNATE_SCREEN_BUFFER=False`、`SUPPORTS_SCROLLBACK=False`。
- `PyteScreenObserver.resize(rows, cols)` 已实现（`Screen.resize`），契约要求随 PTY 一起改尺寸。
- **关键实测结论**：pyte 不足以承担"网页 TUI 状态恢复"。权威快照应来自实现备用屏缓冲的完整仿真器
  （xterm.js + serialize addon）。该取舍列入待决策（见 §7）。

### 2.7 `AutomationDriver`：CBC 业务菜单留在 driver

公共核心只提供 `AutomationContext{terminal_id, runtime, observer, control, attachments}`，暴露
`send_text / send_keys / screen_text / wait_for`。公共核心**不含**任何 CBC 菜单字面量（M11.3 断言：
`pty_contract.py` 中不存在 `"Restore and fork the conversation"` / `"Never Mind"`）。
`navigate_to_anchor`、`_settled_selected_row`、`_await_restore` 这类业务状态机整体留在 driver 层。

### 2.8 所有权策略与 fail-closed 工厂（**布局中立，不绑死服务持 Job / runner 布局**）

产品语义已由用户决定（默认终端随 Pan 服务生死；显式 detach 保留原 PTY/进程；浏览器断连只释放连接），
本节不再重复索要这些决定，只固化**接口形状**与**拒绝条件**。

```python
class OwnershipMode(str, Enum): SERVICE = "service"; DETACHED = "detached"; EXTERNAL = "external"

@dataclass(frozen=True)
class OwnershipPolicy:
    mode: OwnershipMode
    lifecycle_owner: str            # "pan-service" | "runner" | "external-host" —— 仅数据，不参与分支
    tree_guard_kind: str            # "job-object" | "process-group" | "none"
    tree_guard: TreeTerminator | None
    lease_grace_seconds: float | None = None
    detached: bool = False
    def describe() -> dict; def on_service_shutdown(runtime) -> CleanupReport; def reconnect_hint(id) -> dict

def build_runtime(terminal_id, backend, *, ownership, acknowledge_unowned_tree=False, ...) -> PtyRuntime
```

**布局中立性如何体现**：`lifecycle_owner` 只是声明性数据，公共核心**不按它分支**；`tree_guard` 是
注入的 `TreeTerminator` 实现（Job Object / 进程组 / 其它都可以），接口不限定由谁持有句柄。
实测（M16.5）：`lifecycle_owner` 取 `pan-service` / `runner` / `external-host` 三者行为一致，
同一套 API 全部可用。因此三种布局都能用同一接口表达而不需要改契约：布局 A（服务持 Job）、
布局 B（runner 出生持 Job，生命周期 TA 推荐并已实测满足默认终止/detach 语义）、
布局 C（A→B Job 句柄所有权移交 —— 经 `DuplicateHandle` 移交**已由生命周期 TA 实测可行**，见 §6A.1；
但 **ConPTY 宿主/IO 接管仍未测**，所以"服务先持 PTY 再整体 detach"中的 IO 归属问题没有答案）。

**fail-closed 工厂拒绝条件**（任一条不过就抛错，绝不静默降级）：

| 条件 | 异常 |
|---|---|
| `ownership is None`（无默认布局） | `OwnershipPolicyRequired` |
| 声明了 `tree_guard_kind` 但 `tree_guard is None` 且未显式确认 | `UnownedTreeRejected` |
| `mode=DETACHED`（本原型无内核级守卫实现；生产前置项未解决） | `DetachedOwnershipNotImplemented` |
| `mode=EXTERNAL`（外部所有者语义未定义，探针只覆盖 runner 自持布局） | `ExternalOwnershipNotYetValidated` |
| 声明了树守卫但 `start()` 未给启动所有权证据 | `OwnershipGateError` |

`JobObjectTreeTerminator` 在本阶段**故意只声明不实现**（构造即抛 `NotImplementedError` 并列出待定项），
避免把公共契约钉在某一种 Job 布局上，也避免把探索探针当成生产安全实现（§6A.1）。`PsutilTreeTerminator` 与 `NullTreeTerminator` 是探针级实现，
`NullTreeTerminator` 必须配 `acknowledge_unowned_tree=True` 才可用（真实探针就是这么标注的）。

### 2.9 启动所有权门禁与清理身份核验

MA 追加要求的三项生产层约束，在契约层固化为**四要素**门禁（M16.6–M16.11）：

```python
@dataclass(frozen=True)
class ProcessOwnershipEvidence:
    assigned: bool                  # 必须为真（assign 失败不得 running）
    atomic_with_spawn: bool         # 必须为真（消除 spawn→assign 逃逸窗口）
    identity: ProcessIdentity | None    # PID + 100ns FILETIME
    handle_bound_for_cleanup: bool  # 清理用赋值时的同一 handle
    guard: str
```

- **spawn 后 assign 窗口**：`atomic_with_spawn` 为假即拒绝启动（挂起创建→赋值→恢复是唯一可接受形态）。
- **assign 失败拒绝 running**：`assigned` 为假即拒绝；实测状态保持 `created`，从未进入 `running`。
- **清理身份查验 + 同一 handle**：`identity` 缺失或 `handle_bound_for_cleanup` 为假都拒绝启动；
  `close()` 在**任何终止动作之前**核验身份，不匹配或探针失败即**拒杀**并把状态置
  `cleanup-failed` + 保留 owner（M17、R8）。`ProcessIdentity.matches` 对 FILETIME 精确比较、
  无可比字段视为不匹配（fail-closed）。
- 门禁证据中的身份会被 runtime 采用，作为后续清理核验的依据（不会事后另取一个身份）。

**取消原语与其副作用**（R9 实测）：pywinpty 无读超时，"关闭 pty 句柄"是唯一可用的取消手段，且它
**会同时终止进程**；因此 `close()` 只在"终止 + 整树"都成功后才允许取消，否则会毁掉"进程仍存活、
owner 待重试"的证据（R5b.6b 断言）。取消触发的结束原因单列为 `cancelled`（见 §2.3）。

---

## 3. `_PtySession` 逐项映射（PR `packages/core/rewind/driver.py` @ `387a43ec`）

| PR 位置/成员 | PR 现行为 | 公共契约对应 | 差异与风险 |
|---|---|---|---|
| `_PtySession.__init__` L477–490 | 无条件 `from winpty import PtyProcess`；`PtyProcess.spawn(argv, cwd, dimensions, env)`；`pyte.Screen` 固定尺寸；daemon reader | `WinptyBackend`（延迟导入 + `BackendUnavailableError`）+ `PtyRuntime.start(rows, cols)` | 平台依赖不再阻断其它平台；尺寸成为可变状态而非构造参数 |
| `self.queue` L484 | `queue.Queue()` **无界**，`_read` 一直 put | `OutputLog(max_bytes)` + 独立游标 | 无界队列在长期终端上等于内存泄漏（审查 §1 实测 8 Mi 排队） |
| `_read` L492–502 | 先 `if not self.proc.isalive(): queue.put(None); return`，**再** `read(4096)` | `_drain_loop`：循环 `read`，`EOFError` 才结束 | R1 实测：该顺序会丢尾输出；真实 ConPTY 上可 100% 复现 |
| `read(4096)` 固定块 | 4 KiB/次 | `read_size`（默认 64 KiB） | 减少长输出的循环次数；不影响语义 |
| `text()` L504–505 | `chr(10).join(screen.display).rstrip()` | `ScreenObserver.snapshot()`（带 `fidelity`/模式/光标/滚动声明） | 固定 `Screen.display` 就是"单缓冲文本"，无备用屏/无滚动（R6 实测） |
| `wait_for(predicate, deadline)` L507–521 | 从 queue 取块、`stream.feed`、`predicate(text)`；队列 `None` 且进程不活则 break；超时后 `return predicate(last), last` | `wait_for(predicate, timeout, quiet_ms=0) -> WaitOutcome` | 契约增加 `timed_out`/`observed_bytes`/`screen`；`quiet_ms` 用于等屏幕稳定（PR 的 `_settled_selected_row` 是手写的 re-stabilize 循环，可被 quiet_ms 取代）。PR 的返回值无法区分"限时内命中"与"限时结束后最后一次文本恰好满足"，这正是菜单判定容易假阳性的地方 |
| `send(value)` L523–524 | 直接 `proc.write(str)`，无权限/无控制权概念 | `AttachmentRegistry.send(token, bytes)`，需 control lease | 多客户端场景必须拒绝非控制者与迟到消息（M8.6–M8.10） |
| 无 resize | 只在 spawn 传 `dimensions` | `PtyRuntime.resize` + `observer.resize`；由 control lease 授权 | R3 实测子控制台随 resize 变化；R6.11 实测 TUI 运行中 resize 触发重绘（1938 字节） |
| `close()` L526–555 | `\x03` → sleep 0.15 → 线程内 `terminate(force=True)`（1.5s 超时）→ `_kill_tree(pid)` → `proc.close()`；结果字典无状态语义；未 join reader | `PtyRuntime.close() -> CleanupReport` | 契约补齐：reader join、所有权先快照、失败保留 owner、状态迁移（`exited` vs `cleanup-failed`） |
| `_kill_tree(pid)` L558–582 | 先 `terminate`，后 `psutil.Process(pid).children()`；根已消失 → 返回 `[]` | `TreeTerminator`：`owned_pids`（**终止前**）→ `terminate_tree` → `remaining` | 契约修正顺序；R5.5 报告 `owned=[49612, 6548]`（旧顺序会得到空集）。Windows 正式实现仍应使用 spawn 时建立的 Job Object 所有权 |
| 无 Terminal ID / registry | 无 | `TerminalRegistry` + `AttachmentRegistry` | 长期终端管理必需（审查 §4） |
| 无输出游标/退出码事件 | 无 | `read_from(cursor)` + `ExitInfo` | 重连与断点续传的基础 |
| `_await_restore` L180–231 | 直接用 `session.proc.isalive()` + `session.text()` + `session.wait_for` | driver 只依赖 `AutomationContext` | 业务判定与传输解耦；`proc` 不再外泄给 driver |
| `navigate_to_anchor` L800–871 / `_settled_selected_row` L781–797 | 直接 `session.send(chr(27)+"[A")`、`session.wait_for`、`session.text()` | 同样落在 driver 层，通过 `ctx.send_keys/screen_text/wait_for` | **CBC 业务菜单与键位语义留在 driver**，公共核心不实现 |
| 无所有权策略 / 无启动门禁 | spawn 后由上层各自决定，无显式声明 | `OwnershipPolicy` + `build_runtime`（fail-closed）+ `ProcessOwnershipEvidence` 四要素门禁（§2.8/§2.9） | 布局中立；assign 失败/未原子赋值/缺身份/未绑定 handle 一律拒绝进入 `running` |
| `rewind()` 失败即关闭 | `finally: session.close()` | 状态机 + `CleanupReport`（含 `reader_converged` / `identity_check`） | 失败时不谎报 `exited`，保留 owner 可重试 |

**接入结论**：`_PtySession` 不需要被"扩充成长期终端管理器"（审查结论不变）。PR 侧接入方式是让
`_PtySession` 变成 `PtyRuntime + WinptyBackend` 的薄封装，`rewind()` 的菜单逻辑改写成
`AutomationDriver`，从而**同一公共核心同时服务交互终端与回滚自动化**。

---

## 4. 实测证据（REAL：真实 pywinpty/ConPTY + 真实子进程）

命令（可复现，依赖装在临时环境）：

```bash
cd D:/project/pan-worktrees/terminal-contract-explore-20261003
uv run --no-project --python "E:/software/miniforge/python.exe" \
    --with "pywinpty==3.0.5" --with "pyte==0.8.2" --with psutil \
    -- python audit/terminal/contract/probe_real.py \
    --json-out audit/terminal/contract/evidence/probe_real.json
# 迭代调试：追加 --only r1,r6
```

最近一次完整运行：**76 passed / 0 failed / 113.1s**（退出码 0）。JSON 证据：
`audit/terminal/contract/evidence/probe_real.json`。

| 用例 | 关键测量 | 结果 |
|---|---|---|
| R0 | probe pid 49532（创建时间 2026-10-03T00:46:10）、`network_ports_opened: []` | 环境与身份已核验 |
| R1.A | 短命 shell 命令：`output_complete=true`、`drain_stop_reason=eof`、`code=7`、marker 完整、reader 收敛、清理报告 `reader_converged` | PASS（7 项） |
| R1.B1 | 进程先退出再开始读：PR 控制流 `pr_flow_bytes=0`，随后读到 `65` 字节且含 marker，2/2 次（子进程 pid 47240 / 4184） | PASS（3 项） |
| R1.B2.x | 持续读取（时序相关）：PR 控制流 65 字节、`after_alive_false=0`，2/2 次 | MEASURE（如实记录未命中） |
| R2 | 交互 `cmd.exe`：`set AAEVAR=42` 后 `echo AAE-MARKER-%AAEVAR%` → 输出 `AAE-MARKER-42`（证明真执行而非回显），`exit 5` → `code=5` | PASS（5 项） |
| R3 | `mode con` 报 (24,80) → resize(40,132) → 报 (40,132)；旧 lease resize 被拒且终端仍为 (40,132) | PASS（5 项） |
| R4 | 1,240,033 字节真实输出、客户端全程不读：`retained=262,136 ≤ 262,144`、`dropped=977,897`、`gap=(0,977897)`、`code=0`、28.8s 内正常结束 | PASS（8 项） |
| R5 | 真实后代 `ping.exe`（pid 6548，创建时间 00:47:34）→ close 后消失；报告 `owned=[49612, 6548]`、`remaining=[]`、0.25s | PASS（4 项） |
| R5b | **注入**终止失败：`cleanup-failed`、`owner_retained=true`、后端未关闭、真实进程仍存活（37760, 31520）→ 重试后 `exited` 且进程消失 | PASS（7 项） |
| R6 | 真实 `less.exe` TUI（`E:\Git\usr\bin\less.EXE`）：进入 `?1049h`、快照 `alternate_screen=true`、`private_modes={1,7,25,1004,1049,9001}`、屏幕 23 行样本行、`scrollback_lines=0`；翻页重绘 1526B；resize 重绘 1938B 且快照尺寸 (30,100)；退出 `?1049l` 后模式复位但主屏未恢复；尾部 512B 重建与完整解析不一致 | PASS（11 项）+ 2 MEASURE |
| R7 | 客户端离线期间 PTY 继续跑：`total=216,060`、`retained=65,475`、`gap=(0,150,585)`、尾部标记仍在 | PASS（4 项） |
| R8 | 真实身份核验：录得 FILETIME `134354347461843768`；注入 +1s 的身份后清理**拒绝终止**（`refused-identity-mismatch`）、真实进程仍存活；换回真实身份重试才 `exited` 且进程消失 | PASS（8 项） |
| R9 | reader 回收两路径：正常路径 `cancel_kind=none`/`eof`/0.25s 自然收敛；注入跳过 terminate 后阻塞在 read 上的 reader 经"等满 0.5s grace → 关句柄取消"收敛（`cancel_kind=backend-close`、0.875s、`drain_stop_reason=cancelled`、`channel_eof=False`、`output_complete=False`） | PASS（10 项） |
| C1–C3 | 全部自建进程已清理（`leftover: []`）、临时目录已删除、11 个 runtime 清单 | PASS（2 项）+ MEASURE |

R6 的关键观察（值得单独强调）：退出备用屏后 `pyte` 屏幕上仍是 TUI 残留（`LINE-0026...`），
`pyte_shows_pre_tui_line=false`。**真实 xterm/Windows Terminal 客户端会自己维护 1049 备用屏缓冲**，
所以这不是 ConPTY 缺陷，而是"pyte 单缓冲不能当权威快照引擎"的直接证据。

---

## 5. 契约级测试（MOCK：确定性，不代表真实 CLI 行为）

命令：

```bash
uv run --no-project --python "E:/software/miniforge/python.exe" \
    --with "pyte==0.8.2" --with psutil \
    -- python audit/terminal/contract/probe_mock.py \
    --json-out audit/terminal/contract/evidence/probe_mock.json
```

最近一次：**145 passed / 0 failed / 5.7s**。用例分组：

| 组 | 覆盖 |
|---|---|
| M1–M3 | `OutputLog`：绝对序号、驱逐与 gap 数学、单块超容量、`dropped = total - retained` 恒等、分页读取收敛、非法游标拒绝 |
| M4 | drain 到 EOF：脚本后端 `alive()=False` 但仍有尾部 → 契约捕获 20 字节，同后端的 PR 控制流捕获 0 字节（**与真实 PTY 的 R1 同结论**） |
| M5 | 状态机：初始 `created`、非法跃迁拒绝、未启动即可安全 close、退出后写入被拒、退出后 resize 返回 False、重复 close 幂等 |
| M6 | 清理失败保留 owner：`cleanup-failed` + `owner_retained` + 不关句柄 + 重试才 `exited` |
| M7 | registry 保留失败记录、失败状态禁止 `remove`、成功后移除、未知 ID 报错 |
| M8 | lease：generation 从 1 起、observer 只读、转交后旧客户端 send/resize 均 `StaleLeaseError`、尺寸跟随控制者 |
| M9 | terminal id 唯一 + 独立前缀；detach 扩展点未定型时显式失败（含依赖 lifecycle 说明） |
| M10 | 真实 pyte 解析合成 VT：私有模式需 `<<5` 还原（1049 可见）、备用屏标志、光标、无滚动历史、`fidelity=partial`、尾部文本无状态通道 |
| M11 | driver 边界：只能经 lease 写终端、不含生命周期 API、公共核心无 CBC 菜单字面量 |
| M12 | 结束原因分类：只有 `eof` 才声明输出完整；`channel-error` / `eof-timeout` / `stop-requested` / `cancelled` 都不声明（含"自取消的传输层异常不算通道错误"） |
| M13 | reader 回收：取消有效 → 收敛并 `exited`；取消无效 → `cleanup-failed` + 保留 owner + 拒绝 `exited`，释放后重试才收敛 |
| M14 | lease：撤销后 send/resize/transfer 全拒且无写入（200 次 churn）、伪造 `revocation_id` 被拒、observer 越权被拒、撤销一个 observer 不影响其它、已知边界如实记录 |
| M15 | 输出边界：跨块 UTF-8/CSI/OSC 拼接字节一致、逐块解码会破坏 UTF-8、窗口起点落在 CSI 内部并返回 gap、尾部文本视图丢状态 |
| M16 | 所有权：四种拒绝条件、`lifecycle_owner` 三种取值布局中立、启动门禁四要素逐个拒绝且状态保持 `created`、service/detach 关闭语义 |
| M17 | 清理身份核验：不匹配/探针失败拒杀且未调用 terminate、匹配与"进程已消失"正常收敛、`ProcessIdentity.matches` fail-closed 语义 |

---

## 6. 对既有审查四条反模式的契约修复

| 审查问题（优先级） | 契约修复 | 验证 |
|---|---|---|
| 1. 无界输出队列（高） | `OutputLog` 有界 + 绝对序号 + gap；慢客户端断开并从快照恢复；不阻塞 PTY reader | R4（真实 1.24 MB / 客户端不读）、R7、M1–M3 |
| 2. 退出前 drain 不完整（中） | drain 读到通道 EOF；`process_exit_seen` / `channel_eof` / `output_complete` 分开公布；有界 `eof_grace` 防卡死 | R1（0 vs 65 字节）、M4 |
| 3. close 缺少整树所有权与并行 reader（中） | 所有权**先**快照；失败保留 owner 且不关句柄；join reader 后才关句柄；状态显式化 | R5、R5b、M5、M6 |
| 4. 缺失长期会话能力（产品差距） | `TerminalRegistry`、`term_` 命名空间、输出游标、`ExitInfo`、control lease、resize、observer、driver 边界 | R3、R6、R7、M5、M8、M11 |

---

## 6A. 跨 TA 引用、验收状态与 adapter 无关边界

### 6A.1 生命周期 TA（`4b20683` → 返工 `9bd858e2`，**MA 已接受为探索成果**）

引用其报告 `docs/design/PAN_TERMINAL_LIFECYCLE_JOBS_20261003.md`（只读树
`D:/project/pan-worktrees/terminal-lifecycle-explore-20261003`）。MA 已对返工提交 `9bd858e2`
完成关键代码/证据审查并接受为探索成果，正纳入隔离集成。

**实测（可引用）**

| 事实 | 证据 |
|---|---|
| Job **成员资格**只增不减：运行中进程可被 assign 加入（含嵌套），**无 Remove/Detach API**，逃脱只能出生时 breakaway | `job_object_probe` A/B/E |
| kill-on-close 绑定**不能被"多加一个 Job"中和** | A |
| **Job 句柄所有权可移交**：P→A→B 经 `DuplicateHandle` + IPC 传递；P 关闭自己的句柄后树存活（观察 2.0s）；A 移交后关闭并退出，C/G 的 PID 与 100ns 创建时间不变；**B 关闭最后一个句柄（B 仍存活）瞬间整树死亡 0.0s** | `job_handle_handoff` H0–H10（11/11） |
| 启动 assign 失败 fail-closed：不发布 running 端点、不开控制端口、只清理自有后代后非零退出 | `s6_startup_assign_failure` 9/9 |
| 终止路径改为**单 handle 原子核验 + 终止**（同一 handle 完成 FILETIME/退出码核验与 Terminate），TOCTOU 修复并有负例"核验失败不终止" | K1–K3 |
| 推荐布局（runner 出生自持 PTY + 自持 kill-on-close Job 守卫 + lease 默认终止 + 显式 detach durable）满足正常退出/崩溃/同 PID 重连/整树清理 | s1–s6 67/67 |

**明确未测（不得当作已解决）**

1. **ConPTY host/IO 接管迁移**：未测，其报告不做任何可行性声明，本契约同样不声明。
2. **spawn→assign 启动窗口未消除**：`pywinpty.spawn` 不暴露 creationflags，无法"挂起创建→assign→恢复"；
   该报告自述其探针是"寿命/所有权语义探针，**不是 race-free 生产 spawn**"。
3. **不得照搬探针为生产安全实现**（MA 明确要求）：控制端点在探针里是 loopback + 明文 token，
   产品需要命名管道/ACL 或受限 token。

**对本契约的直接影响**

- 我上一版 §6A.1 引用的是 `4b20683` §4 规则 4 的**推断**（"`DuplicateHandle` 只增引用计数、不转移绑定"）
  并据此说布局 C 待验证。返工实测**推翻了该推断的结论部分**，已按 `9bd858e2` §4.3 修正版更新：
  迁移必须分层表述 —— **成员资格迁移不可行 / 句柄所有权迁移可行且已实测 / PTY 宿主·IO 接管未测**。
  （注：`9bd858e2` 的 §4.2 旧块仍保留该推断句，阅读时应以 §4.3 修正版为准。）
- **接口无需改动**：布局 C 只是 `lifecycle_owner` + `tree_guard` 注入的不同取值，公共契约保持布局中立
  （§2.8）。这正是把"谁持句柄"作为数据而非接口约束的收益。
- 其"单 handle 原子核验 + 终止"与我的 `handle_bound_for_cleanup` + 清理前身份核验（§2.9）是同一要求；
  但双方都**不再使用**"PID 复用不可能误杀"这类绝对措辞（其报告已撤下该措辞，本契约同样只声称
  "身份不匹配即拒杀并保留 owner"）。
- 其 §7.5"公共 backend 实现前置"与我的启动门禁一致：`atomic_with_spawn=False` 即拒绝 `running`，
  所以**当前真实探针显式 `acknowledge_unowned_tree=True` 且 `require_startup_gate=False`**，
  并在证据里标注该门禁未满足。

### 6A.2 CBC 首轮报告边界（供后续整理，不改变本契约方向）

- CBC `--serve` 的 PowerShell PTY 输入/SSE/resize/DELETE 与 ACP `initialize`/`session/new` **分别成功**，
  但**没有**同一个 CBC Agent/backend/turn 与原生 TUI 绑定的证据；当前 headless CLI attach 失败
  **不能排除**其它途径。原 TA 正在 review/纠正。
- 因此本公共 PTY/Terminal core **保持 adapter-independent**：核心接口不出现任何 adapter 专属概念，
  不把 CBC daemon/ACP 列为必选依赖，也不写成"已实现无中断 TUI"。
- 若未来采用 CBC daemon/ACP，它是 **adapter runtime 扩展**（在 driver/扩展层实现），不是核心前提。

---

## 7. 未验证项、失败项与待其他 TA 证据的决定

**未验证（明确不做或无结论）**

1. **显式 runtime detach 的生产实现**：语义侧证据已具备（生命周期 TA s3/s5 同 PID 保活+重连；Job 句柄
   P→A→B 移交 H0–H10 实测；MA 已接受为探索成果，§6A.1）。本契约仍拒绝 `DETACHED`/`EXTERNAL` 的原因
   不是产品决定未定，而是**本原型没有内核级树守卫实现**（`JobObjectTreeTerminator` 故意不实现），
   且生产 backend 前置项未解决：挂起式 spawn/原子入组、命名管道或 ACL 控制端点、ConPTY host/IO 接管未测。
2. **POSIX backend**：未实现、未测（仅 Windows/ConPTY 有实测证据）。
3. **真实浏览器与权威快照**：xterm.js / fit / serialize addon、WS 协议、浏览器渲染均**未实现、未验证**；
   "权威仿真器快照"（能完整覆盖鼠标/粘贴/键盘协议/滚动历史的实现）也未验证——本阶段只有 pyte 的
   `fidelity=partial` 观察者。这两项不能当作已交付能力。
4. **服务生命周期（Pan 服务重启后默认终端结束、detach 终端保留）**：未测；需要在 Pan 服务内集成后才能验证。
5. **未授权客户端隔离 / IPC 仅本机用户 / 真实身份授权**：设计已写（§2.5），未实现、未测；
   已知边界"复制 `revocation_id` 的 token 会被接受"已如实记录（M14.19）。
6. **Agent takeover 与 held/生命周期锁的双 writer 问题**：未触碰。
7. **真实 provider TUI（CBC / Codex）**：本探针刻意未启动 `cbc`/`codex`（避免触碰既有会话与认证）；
   R6 用的是本机真实 `less.exe`（真实备用屏 TUI），不是 provider 原生 TUI（§6A.2）。
8. **Job Object 相关**：spawn→assign 原子性、assign 失败、同一 handle 清理在契约层以四要素门禁固化
   （M16）；生命周期 TA 已在**探针层**给出对应实测（s6 assign 失败 fail-closed 9/9；K1–K3 单 handle
   原子核验+终止），但其自述为"寿命探针，非 race-free 生产 spawn"。**我的真实探针**未在 Job 上验证，
   故显式 `acknowledge_unowned_tree=True` 且 `require_startup_gate=False`。
9. **控制端点安全模型**：探针是 loopback + 明文 token；命名管道/ACL/受限 token 未实现（与生命周期 TA
   §7.6 同一结论）。本契约只做进程内单 writer，**不做身份授权**（§2.5）。

**已知失败/风险（如实记录）**

1. **R1.B2（持续读取）未命中竞态**：真实 ConPTY 上"进程退出后仍有可读字节"是时序相关的，持续读取时 2/2 次未命中。
   因此不能声称"PR 每次短命令都丢尾输出"；正确的结论是"PR 的停止条件不是 EOF，因此在进程先退出的情形下会丢尾部
   （已确定性复现）"。
2. **取消原语的副作用**：pywinpty 上"关句柄"既是唯一取消手段、又会终止进程（R9 实测）。因此
   `close()` 只在终止+整树成功后取消；若后端将来提供更细粒度的读端关闭，应替换 `_cancel_drain` 的实现。
   现有证据只覆盖 pywinpty，未覆盖其它后端。
3. **`PsutilTreeTerminator` 不是整树所有权的证明**：R5.3 的"后代消失"部分由 ConPTY 关闭时终止附着进程达成，
   而 R5.5 的 `remaining=[]` 只覆盖"先前快照到的 PIDs"。正式实现必须用 Job Object 或等价内核级守卫。
4. **输出边界不是序列边界**（已纠正先前表述）：跨块/窗口起点都可能切断 UTF-8、CSI、OSC；gap 边界不保证落在
   序列边界上，客户端必须走快照恢复（M15）。
5. **R4 的 30.4 秒**是 `cmd` 的 `for /L` 生成速度，不是 PTY 吞吐上限；探针未测 PTY 吞吐上限。
6. **M14.19 已知边界**：token 内容等价即可通过（`revocation_id` 不是对已读 token 者的秘密）。

**待其他 TA 证据才能定型的决定（需 MA 归档）**

| 决定 | 依赖 | 状态 |
|---|---|---|
| Job 布局选择与生产落地 | 生命周期 TA 布局 B 已被 MA 接受为探索成果；句柄移交 C 已实测可行（`9bd858e2`）。落地还需生产 backend 加固（挂起式 spawn/原子入组、ACL 控制端点） | 方向已定，待实施 |
| ConPTY host/IO 是否可迁移 | 需要专门探针（生命周期 TA 与契约均未测） | 未决 |
| 屏幕快照引擎选型（pyte 只作自动化观察 vs xterm.js 作权威快照） | CBC/Codex TUI 探针：实际用到的模式（鼠标/粘贴/键盘协议/滚动） | 未决 |
| 是否提供"保留原进程的无中断 TUI 切换"及首版范围 | Codex 被测目标 turn 在 TUI 退出后中断；CBC 未证实同 Agent 切换 | MA 已定：首版普通持久 PTY/Web 终端优先，原生无中断 TUI 延后 |
| `mode=EXTERNAL` 的默认寿命/崩溃/重连语义 | 生命周期 TA 只覆盖 runner 自持布局，外部所有者语义未定义 | 未决 |
| 旧客户端 gap 恢复的产品语义（自动重放 vs 强制重连） | MA 产品决定 | 未决 |
| 输出保留窗口大小与浏览器 ack 协议 | 与前端一起定 | 未决 |

> 已由用户决定、本契约**不再重复索要**的语义：默认终端随 Pan 服务生死；浏览器/面板隐藏或断连只释放连接；
> 显式关闭终端才终止；显式 runtime detach 后保留原 PTY/进程并可重连。这些已固化进 §2.8 的策略形状。

---

## 8. 接口影响与下一步建议

**接口影响**

- 新增公共核心（建议位置：`packages/core/terminal/`）：`pty_contract.py` 的接口可直接作为实现骨架。
  正式实现**必须**替换：`PsutilTreeTerminator` → 内核级树守卫（Job Object 或等价物，布局由 §7 决定）；
  `PyteScreenObserver` → 权威快照引擎（或明确降级为"自动化观察专用"）。公共核心**保持 adapter-independent**
  （§6A.2），不引用任何 adapter 专属概念。
- **生产层必须解决的三项**（契约已用门禁固化，但真机 Job 未验证）：
  ① spawn→assign 原子化（挂起创建→赋值→恢复），消除逃逸窗口；② assign 失败/未原子赋值/缺身份/
  未绑定 handle ⇒ **拒绝进入 `running`**；③ 清理前核验身份（PID + FILETIME）、用赋值时的同一 handle 终止。
- PR #6 接入：`_PtySession` 变成 `PtyRuntime` 薄封装，`rewind()` 拆出 `AutomationDriver`。
  **不改变回滚对外语义**，回滚测试（62 项）应保持通过；失败路径需区分 `exited` / `cleanup-failed`，
  并且**不能**沿用"先判 isalive 再 read"的停止条件（R1）。
- 对 `packages/core/background_jobs.py`：终端与 Pan Job 仍是两类对象；可共用底层 runtime owner/注册表/
  身份核验代码路径，但**必须参数化生命周期策略**而非复用其"默认随 Pan 存活"语义。本阶段未改其代码。
- 依赖：正式接入需要新增 `pywinpty`（Windows 限定）+ 屏幕仿真依赖；本阶段未改锁文件。

**下一步建议（按依赖顺序）**

1. MA 归档本报告 + `audit/terminal/contract/`；生命周期 TA `9bd858e2` 已获接受，剩余需列入明确验证的只有
   **ConPTY host/IO 接管可迁移性**（双方均未测）与生产 backend 加固项（挂起式 spawn/原子入组、ACL 控制端点）。
2. 实施阶段先落地 `PtyBackend + OutputLog + PtyRuntime + build_runtime`（无 UI），把 PR #6 回滚接到公共核心
   并跑回滚测试；同时按 §2.9 接入门禁（真机 Job 赋值 + 身份核验）。
3. 快照引擎选型前不承诺"网页 TUI 状态恢复"；`fidelity` 字段已为降级留出表达空间。
4. 终端 registry / lease 落地后再做 WS 与 xterm.js 前端；浏览器侧与权威快照（serialize addon）都属未验证项。
5. 所有权布局一律经 `OwnershipPolicy` 注入；`JobObjectTreeTerminator` 实现前不放开 DETACHED/EXTERNAL。

---

## 附录 A：交付文件清单

| 文件 | 说明 |
|---|---|
| `audit/terminal/contract/pty_contract.py` | 公共契约原型（接口 + 参考实现 + 所有权工厂 + 确定性测试后端） |
| `audit/terminal/contract/probe_lib.py` | 探针工具（结果记录/JSON/进程身份核验/硬超时看门狗） |
| `audit/terminal/contract/probe_real.py` | REAL 探针（R1–R9，真实 PTY + 真实子进程） |
| `audit/terminal/contract/probe_mock.py` | MOCK 探针（M1–M17，确定性契约逻辑） |
| `audit/terminal/contract/README.md` | 运行方式与边界声明 |
| `audit/terminal/contract/evidence/probe_real.json` | REAL 运行 JSON（76 passed / 0 failed） |
| `audit/terminal/contract/evidence/probe_mock.json` | MOCK 运行 JSON（145 passed / 0 failed） |
| `docs/design/PAN_TERMINAL_PTY_CONTRACT_20261003.md` | 本文档 |

## 附录 B：清理核验

- 自建 runtime 11 个（`term_probe_00..10`）；额外记录 PID：7060（`ping.exe`）、33556（`ping.exe`，01:12:07 创建）；
  R8/R9 的身份核验与取消用例的进程也都在用例内单独核对（R8.9 确认消失、R9 兜底清理）。
- 结束核对：`leftover: []`（无残留进程）；临时目录 `%TEMP%\pan_pty_contract_probe_*` 已删除。
- 未打开监听端口；未触碰任何既有服务/Session/Worker/CLI thread。
- 未修改 main / practical / 其它 TA 工作树 / `.workflow`；跨 TA 引用仅**只读**读取生命周期 TA 的已提交内容。

## 附录 C：官方参考

- Microsoft ConPTY（创建、尺寸、退出、持续 drain）：<https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session>
- pywinpty：<https://github.com/andfoy/pywinpty>
- pyte：<https://github.com/selectel/pyte>
- xterm.js 与 serialize/fit addon、流控指南：<https://github.com/xtermjs/xterm.js/>、<https://xtermjs.org/docs/guides/flowcontrol/>
