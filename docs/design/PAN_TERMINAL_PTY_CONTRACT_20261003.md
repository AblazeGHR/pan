# Pan Terminal / PTY 公共契约与 PR #6 接入设计

- 业务任务：T-TERMINAL-PTY-20261003（公共 PTY 契约与 PR #6 接入，effort high）
- 日期：2026-10-03
- 工作树：`D:/project/pan-worktrees/terminal-contract-explore-20261003`
- 分支：`explore/terminal-contract-20261003`，起点 `35b6fb1abaae9d741b3042470aa32a8c356e431d`
- PR 只读源码树：`D:/project/pan-worktrees/pr6-terminal-source-20261002`（`387a43ec4dfe699498bb0b95d7ceef212f905b99`）
- 交付物：`audit/terminal/contract/`（契约原型 + 可运行探针 + JSON 证据）、本文档
- 性质：**探索阶段交付**。未修改 `packages/` 下任何正式模块，未改正式依赖锁，未合入/推送，未重启任何服务。

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
| C10 | terminal 命名空间与 session/worker 分离、lease generation、状态机合法性等契约逻辑全部通过（mock 73 项 0 失败） | 针对性测试（M1–M11） |

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
| 驱逐 | 以**整块**为单位，不把一次 VT 序列从中间切断；单块大于上界时保留尾部并计入 gap |
| `read_from(cursor, max_bytes=None) -> OutputPage` | 返回 `chunks / first_seq / next_cursor / gap / truncated` |
| gap | `cursor < first_retained_seq` 时返回 `(cursor, first_retained_seq)`；**不补零**，要求客户端走快照恢复 |
| 非法游标 | `cursor > total` 抛 `InvalidCursorError` |

策略选择（需 MA 确认产品语义）：**单条有界历史 + 快照恢复**，而**不是**每客户端独立缓冲。理由：慢客户端若各自排队
就是 PR 的无界队列问题换了个位置；契约要求慢到丢窗口的客户端断开并从快照恢复。浏览器确认游标应在其渲染完成后上报。

### 2.3 `PtyRuntime`：所有权 + drain + 三个退出事实

状态机（`_LEGAL_TRANSITIONS` 强制，非法跃迁抛 `IllegalStateTransition`）：

```
CREATED ──> STARTING ──> RUNNING ──> EXITING ──> EXITED
   │                        │            └─────> CLEANUP_FAILED ──> EXITING（重试）
   └──> EXITING             └──> LOST（通道错误/无法收敛）
```

**三个不同事实，分开公布**（对应审查 §2 的"区分根进程退出、通道关闭和终端完成"）：

| 字段 | 含义 | 来源 |
|---|---|---|
| `process_exit_seen` + `code` | 根进程已退出、退出码 | `exit_code()` |
| `channel_eof` | 读端到达 EOF | `read()` 抛 `EOFError` |
| `output_complete` | 输出已完整（不再有字节） | drain 线程结束 |

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

### 2.8 能力扩展点：默认 managed / 显式 detach

```python
class OwnershipMode(str, Enum): SERVICE = "service"; DETACHED = "detached"
class OwnershipPolicy(Protocol): on_service_shutdown(report) -> CleanupReport; reconnect_hint(id) -> dict
def require_detached_ownership(policy) -> None   # 未定型时抛 DetachedOwnershipNotImplemented
```

本阶段**只给接口，不给实现**：显式 detach 需要"保留原 PTY/进程 + 同 PID 重连"的证据，属生命周期 TA 的范围。
`require_detached_ownership` 故意**显式失败**而不是静默降级为"重启代替 detach"（M9.4 断言）。default managed 不需要该策略。

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
| `rewind()` L619–778 `finally: session.close()` | 回滚结束即销毁 PTY | 上层用 runtime 状态机决定 `exited`/`cleanup-failed`/重试 | PR 语义（临时 PTY）保持可用；长期终端另走 registry |

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

最近一次完整运行：**56 passed / 0 failed / 106.8s**（退出码 0）。JSON 证据：
`audit/terminal/contract/evidence/probe_real.json`。

| 用例 | 关键测量 | 结果 |
|---|---|---|
| R0 | probe pid 49532（创建时间 2026-10-03T00:46:10）、`network_ports_opened: []` | 环境与身份已核验 |
| R1.A | 短命 `cmd /c echo ... & exit /b 7`：`output_complete=true`、`code=7`、marker 完整、reader 收敛 | PASS（6 项） |
| R1.B1 | 进程先退出再开始读：PR 控制流 `pr_flow_bytes=0`，随后读到 `65` 字节且含 marker，2/2 次（子进程 pid 47240 / 4184） | PASS（3 项） |
| R1.B2.x | 持续读取（时序相关）：PR 控制流 65 字节、`after_alive_false=0`，2/2 次 | MEASURE（如实记录未命中） |
| R2 | 交互 `cmd.exe`：`set AAEVAR=42` 后 `echo AAE-MARKER-%AAEVAR%` → 输出 `AAE-MARKER-42`（证明真执行而非回显），`exit 5` → `code=5` | PASS（5 项） |
| R3 | `mode con` 报 (24,80) → resize(40,132) → 报 (40,132)；旧 lease resize 被拒且终端仍为 (40,132) | PASS（5 项） |
| R4 | 1,240,033 字节真实输出、客户端全程不读：`retained=262,136 ≤ 262,144`、`dropped=977,897`、`gap=(0,977897)`、`code=0`、28.8s 内正常结束 | PASS（8 项） |
| R5 | 真实后代 `ping.exe`（pid 6548，创建时间 00:47:34）→ close 后消失；报告 `owned=[49612, 6548]`、`remaining=[]`、0.25s | PASS（4 项） |
| R5b | **注入**终止失败：`cleanup-failed`、`owner_retained=true`、后端未关闭、真实进程仍存活（37760, 31520）→ 重试后 `exited` 且进程消失 | PASS（7 项） |
| R6 | 真实 `less.exe` TUI（`E:\Git\usr\bin\less.EXE`）：进入 `?1049h`、快照 `alternate_screen=true`、`private_modes={1,7,25,1004,1049,9001}`、屏幕 23 行样本行、`scrollback_lines=0`；翻页重绘 1526B；resize 重绘 1938B 且快照尺寸 (30,100)；退出 `?1049l` 后模式复位但主屏未恢复；尾部 512B 重建与完整解析不一致 | PASS（11 项）+ 2 MEASURE |
| R7 | 客户端离线期间 PTY 继续跑：`total=216,060`、`retained=65,475`、`gap=(0,150,585)`、尾部标记仍在 | PASS（4 项） |
| C1–C3 | 全部自建进程已清理（`leftover: []`）、临时目录已删除、8 个 runtime 清单 | PASS（2 项）+ MEASURE |

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

最近一次：**73 passed / 0 failed / 3.1s**。用例分组：

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

---

## 6. 对既有审查四条反模式的契约修复

| 审查问题（优先级） | 契约修复 | 验证 |
|---|---|---|
| 1. 无界输出队列（高） | `OutputLog` 有界 + 绝对序号 + gap；慢客户端断开并从快照恢复；不阻塞 PTY reader | R4（真实 1.24 MB / 客户端不读）、R7、M1–M3 |
| 2. 退出前 drain 不完整（中） | drain 读到通道 EOF；`process_exit_seen` / `channel_eof` / `output_complete` 分开公布；有界 `eof_grace` 防卡死 | R1（0 vs 65 字节）、M4 |
| 3. close 缺少整树所有权与并行 reader（中） | 所有权**先**快照；失败保留 owner 且不关句柄；join reader 后才关句柄；状态显式化 | R5、R5b、M5、M6 |
| 4. 缺失长期会话能力（产品差距） | `TerminalRegistry`、`term_` 命名空间、输出游标、`ExitInfo`、control lease、resize、observer、driver 边界 | R3、R6、R7、M5、M8、M11 |

---

## 7. 未验证项、失败项与待其他 TA 证据的决定

**未验证（明确不做或无结论）**

1. **显式 runtime detach / 同 PID 重连 / 父进程崩溃语义**：未实现（只有接口），属生命周期 TA。
   Windows Job Object 所有权应在 spawn 时建立，本探针用 psutil 兜底并已知其顺序局限。
2. **POSIX backend**：未实现、未测（仅 Windows/ConPTY 有实测证据）。
3. **网页前端（xterm.js / fit / serialize）与 WS 协议**：未实现。这正是本阶段不承诺的能力。
4. **服务生命周期（Pan 服务重启后默认终端结束、detach 终端保留）**：未测；需要在 Pan 服务内集成后才能验证。
5. **未授权客户端隔离 / IPC 仅本机用户**：设计已写（§2.5），未实现、未测。
6. **Agent takeover 与 held/生命周期锁的双 writer 问题**：未触碰。
7. **真实 provider TUI（CBC / Codex）**：本探针刻意未启动 `cbc`/`codex`（避免触碰既有会话与认证）；真实 TUI 证据
   由 CBC/Codex TA 提供。R6 用的是本机真实 `less.exe`（真实备用屏 TUI），不是 provider 原生 TUI。

**已知失败/风险（如实记录）**

1. **R1.B2（持续读取）未命中竞态**：真实 ConPTY 上"进程退出后仍有可读字节"是时序相关的，持续读取时 2/2 次未命中。
   因此不能声称"PR 每次短命令都丢尾输出"；正确的结论是"PR 的停止条件不是 EOF，因此在进程先退出的情形下会丢尾部
   （已确定性复现）"。这也说明该缺陷的真实触发条件依赖读取时序。
2. **reader 可能在 `close()` 之后仍阻塞在 `read()`**：`pywinpty` 无读超时；契约用 `join(timeout)` 收敛，超时后
   `reader_joined=false`。该情形下 reader 是 daemon 线程、不再写入已关闭的句柄，但**尚未证明**在所有后端下都不会
   延迟到进程退出后才回收。需要后端提供"关闭读端"原语（正式实现时确认）。
3. **`PsutilTreeTerminator` 不是整树所有权的证明**：R5.3 的"后代消失"部分由 ConPTY 关闭时终止附着进程达成，
   而 R5.5 的 `remaining=[]` 只覆盖"先前快照到的 PIDs"。正式实现必须用 Job Object。
4. **`OutputLog` 的整块驱逐**：单块极大时（> 上界）保留尾部并产生 gap；这意味着 gap 可能落在**一个块内部**。
   契约允许，但要求客户端一律走快照恢复，不能假设 gap 边界与块边界对齐。
5. **R4 的 29.3 秒**是 `cmd` 的 `for /L` 生成速度，不是 PTY 吞吐上限；探针未测 PTY 吞吐上限。

**待其他 TA 证据才能定型的决定（需 MA 归档）**

| 决定 | 依赖 |
|---|---|
| 屏幕快照引擎选型（pyte 只作自动化观察 vs xterm.js 作权威快照） | CBC/Codex TUI 探针：TUI 实际用到哪些模式（鼠标/粘贴/键盘协议/滚动） |
| 是否提供"保留原进程的无中断 TUI 切换"及首版范围 | CBC TA / Codex TA |
| 默认 managed 与显式 detach 的产品承诺边界、重连方法 | 生命周期 TA（同 PID 重连、父崩溃、Job 限制） |
| detach 后是否需要独立 host 进程 | 生命周期 TA |
| 旧客户端 gap 恢复的产品语义（自动重放 vs 强制重连） | MA 产品决定 |
| 输出保留窗口大小与浏览器 ack 协议 | 与前端一起定 |

---

## 8. 接口影响与下一步建议

**接口影响**

- 新增公共核心（建议位置：`packages/core/terminal/`）：`pty_contract.py` 的接口可直接作为实现骨架，
  但**必须**在正式实现里替换两处：`PsutilTreeTerminator` → 基于 Job Object 的 `TreeTerminator`；
  `PyteScreenObserver` → 权威快照引擎（或明确降级为"自动化观察专用"）。
- PR #6 接入：`_PtySession` 变成 `PtyRuntime` 薄封装，`rewind()` 拆出 `AutomationDriver`。
  **不改变回滚对外语义**，回滚测试（62 项）应保持通过；`rewind()` 失败路径改为区分 `exited` / `cleanup-failed`。
- 对 `packages/core/background_jobs.py`：终端与 Pan Job 仍是两类对象；可共用底层 runtime owner，
  但本阶段未修改其代码。Job 注册/日志/身份核验的比较留给生命周期 TA。
- 依赖：正式接入需要新增 `pywinpty`（Windows 限定）+ 屏幕仿真依赖；本阶段未改锁文件，由 MA 在实施阶段决策。

**下一步建议（按依赖顺序）**

1. MA 归档本报告 + `audit/terminal/contract/`，确认 §7 的 6 项决定里哪些可先定。
2. 在实施阶段先落地 `PtyBackend + OutputLog + PtyRuntime`（无 UI），把 PR #6 的回滚接到公共核心并跑回滚测试。
3. 快照引擎选型前，不承诺"网页 TUI 状态恢复"；`fidelity` 字段已为降级留出表达空间。
4. 终端 registry / lease 落地后再做 WS 与 xterm.js 前端。
5. detach 一律走生命周期 TA 的结论；`require_detached_ownership` 的显式失败可防止静默降级。

---

## 附录 A：交付文件清单

| 文件 | 说明 |
|---|---|
| `audit/terminal/contract/pty_contract.py` | 公共契约原型（接口 + 参考实现 + 确定性测试后端） |
| `audit/terminal/contract/probe_lib.py` | 探针工具（结果记录/JSON/进程身份核验/硬超时看门狗） |
| `audit/terminal/contract/probe_real.py` | REAL 探针（R1–R7，真实 PTY + 真实子进程） |
| `audit/terminal/contract/probe_mock.py` | MOCK 探针（M1–M11，确定性契约逻辑） |
| `audit/terminal/contract/README.md` | 运行方式与边界声明 |
| `audit/terminal/contract/evidence/probe_real.json` | REAL 运行 JSON（56 passed / 0 failed） |
| `audit/terminal/contract/evidence/probe_mock.json` | MOCK 运行 JSON（73 passed） |
| `docs/design/PAN_TERMINAL_PTY_CONTRACT_20261003.md` | 本文档 |

## 附录 B：清理核验

- 自建 runtime 8 个（`term_probe_00..07`），额外记录 PID：6548（`ping.exe`，00:47:34 创建）、31520（`ping.exe`，00:47:38 创建）。
- 结束核对：`leftover: []`（无残留进程）；临时目录 `%TEMP%\pan_pty_contract_probe_*` 已删除。
- 未打开监听端口；未触碰任何既有服务/Session/Worker/CLI thread。
- 未修改 main / practical / 其它 TA 工作树 / `.workflow`。

## 附录 C：官方参考

- Microsoft ConPTY（创建、尺寸、退出、持续 drain）：<https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session>
- pywinpty：<https://github.com/andfoy/pywinpty>
- pyte：<https://github.com/selectel/pyte>
- xterm.js 与 serialize/fit addon、流控指南：<https://github.com/xtermjs/xterm.js/>、<https://xtermjs.org/docs/guides/flowcontrol/>
