# Pan Terminal 权威仿真器接口（P1 生产实现，2026-10-03）

- 任务：T-TERMINAL-PTY-20261003 的 **P1 常驻 headless 仿真器生产实现**。
- 工作树：`D:/project/pan-worktrees/terminal-emulator-implement-20261003`
  （branch `implement/terminal-emulator-20261003`，起点 `8af9b0f9`）。
- 起点含义：本树已集成 core r3、Windows backend 基础安全子集、IPC/秘密存储安全子集；
  **不等于完整终端交付**。本轮不触碰 frozen 模块与共享文件（见 §1）。
- 引擎候选来源（只读）：`docs/design/PAN_TERMINAL_HEADLESS_SNAPSHOT_SPIKE_20261003.md`
  （r2 + MA 集成审查）；探索实现 `audit/terminal/cbc/emulator/` **只作参考，不照搬其保障**：
  MA 指出的五项探索残留在本轮均以"先失败后通过"方式修复（§10）。

**诚实声明（贯穿全文）**

- 本文件描述的是 **headless↔headless** 引擎宿主；**没有运行真实浏览器**，不声明浏览器
  渲染兼容性（P3 门）。
- 不声明任何"无中断原生 TUI"能力；不涉及任何真实 provider/模型/认证。
- `full` 只可能在"未检测到任何缺口/未知序列 + 无 lag/dirty/gap/reset/错误"时出现；
  未测特性一律 `partial`（§7）。

---

## 1. 交付与写范围

| 文件 | 内容 |
| --- | --- |
| `packages/core/terminal/emulator.py` | `HeadlessEmulator`：`AuthoritativeEmulator` 实现 + runner 桥接（`feed_at`/`restore_screen`）+ sidecar 进程所有权 |
| `emulator_sidecar/sidecar.mjs` | 常驻 Node sidecar：帧协议、hold-back 供料、保守分类器、barrier 快照、reset、sticky 降级 |
| `emulator_sidecar/package.json` + `package-lock.json` | 精确 pin `@xterm/headless@6.0.0`、`@xterm/addon-serialize@0.14.0`（`node_modules/` 由仓库 `.gitignore` 忽略，不提交） |
| `tests/test_terminal_emulator.py` | 28 项测试矩阵（§9） |
| `docs/design/PAN_TERMINAL_EMULATOR_INTERFACES_20261003.md` | 本文件 |
| `audit/terminal/implementation/emulator/**` | 先失败/后通过证据、复现脚本与清理扫描（旧 CBC 证据不覆盖） |

**未改**（保持冻结）：`__init__.py` / `contracts.py` / `runtime.py` / `backend.py` / `guard.py` /
`identity.py` / `ipc.py` / `win_pipe.py` / `secret_store.py` / `spawn_win.py` / `requirements*.txt` /
锁文件 / `server.py` / MCP / 前端 / `background_jobs*` / `runner.py`（本树尚未存在）。
使用方式：`from packages.core.terminal.emulator import HeadlessEmulator`
（未改 `__init__`，与 IPC 子集同约定）。

---

## 2. 引擎与依赖 pin（installed facts）

| 项 | 值 | 证据 |
| --- | --- | --- |
| `@xterm/headless` | **6.0.0**（exact pin） | `emulator_sidecar/package.json` / `package-lock.json` |
| `@xterm/addon-serialize` | **0.14.0**（exact pin） | 同上 |
| Node（本机实测） | `v24.15.0` | `ready` 帧 `node_version`（测试断言不锁定具体版本，仅要求可加载） |
| `allowProposedApi` | **必须为 true**（序列化 addon 依赖） | 实测：不开时 `serialize()` 抛 "You must set the allowProposedApi option" |
| 版本校验 | sidecar 启动时读 `node_modules/@xterm/headless/package.json` 与 addon 版本，**不等于 pin 即拒绝启动**（`fatal: dependency-version-mismatch`） | `sidecar.mjs` 启动段；对应 Python 侧 `EmulatorUnavailableError` |

sidecar 是**纯状态引擎**：不接触 token/DPAPI/命名管道；协议中没有认证字段；
token 不需要跨 sidecar（与 IPC 子集的分层边界一致）。

---

## 3. 对外 Python 接口：`HeadlessEmulator`

### 3.1 `AuthoritativeEmulator` 协议面（冻结于 `contracts.py`）

```python
class HeadlessEmulator:
    def __init__(self, *, cols=80, rows=24, scrollback=1000, start_cursor=0,
                 node_binary=None, sidecar_path=None,
                 max_queue_bytes=4*1024*1024, max_queue_ops=8192,
                 max_pending_tail_bytes=8192,
                 max_rejected_ranges=64, max_reasons=64, max_diagnostics=64,
                 snapshot_timeout=2.0, control_timeout=2.0,
                 startup_timeout=15.0, shutdown_timeout=5.0, kill_timeout=5.0) -> None

    @property
    def feed_lag(self) -> bool: ...
    def feed(self, data: bytes) -> None: ...                       # 协议：从当前期望位置连续供料
    def resize(self, rows: int, cols: int) -> None: ...            # 与 feed 同一条有序队列
    def snapshot(self, *, timeout: float = 2.0) -> AppliedSnapshot: ...
    def reset_baseline(self) -> AppliedSnapshot: ...               # 新基线（见 §7.4）
```

- 构造即启动 sidecar；**Node 缺失/依赖缺失/握手失败**抛
  `EmulatorUnavailableError`（`BackendUnavailableError` 子类），异常文本带清理结果。
  由 runner 决定基本终端策略（降级为无权威快照）。
- 非 Windows 平台直接拒绝（Job Object 所有权是生产宿主前提）。

### 3.2 runner 桥接面（本轮新增，供 runner TA 接线）

```python
    def feed_at(self, seq: int, data: bytes) -> None: ...   # == contracts.OutputConsumer
    def restore_screen(self, serialized_screen: str) -> bool: ...
    def barrier(self, *, timeout: float | None = None) -> dict: ...
    def diagnostics(self) -> dict: ...
    def close(self, *, timeout: float | None = None) -> EmulatorCloseReport: ...
```

**关键接线（冻结接口，不要求 runner 改 contracts）**：

```python
# runner 装配（示意）
emulator = HeadlessEmulator(cols=cols, rows=rows)          # runner 进程内常驻
runtime = build_runtime(
    terminal_id, backend,
    ...,
    output_consumer=emulator.feed_at,                      # (绝对偏移, bytes) 同序投递
)
# resize 联动（同一条有序队列语义在 runner 侧编排）：
runtime.resize(rows, cols); emulator.resize(rows, cols)     # 两者都只由 control lease 触发

# 快照（WS 桥重连/gap 恢复）
snap = emulator.snapshot(timeout=2.0)
# 客户端渲染：restore_screen(snap.serialized_screen) → 从 snap.cursor 重拉原字节续流
```

- `feed_at(seq, data)` 与 `contracts.OutputConsumer = Callable[[int, bytes], None]` 签名一致，
  `seq` 是该块首字节的**绝对偏移**（与 `OutputLog` 同源）；runtime 在同一 reader 临界区
  内先 `log.append` 后回调，本方法**同步、有界、快速返回**（只做偏移核验与入队/拒绝记账，
  不等待 sidecar）。
- `snapshot.cursor` = sidecar **已解析应用**（xterm write 回调确认）的绝对字节位置；
  **不是** producer 的 `total_bytes`（§5.3）。
- `close()` 幂等、可重试；runner 的硬性兜底是自持 Job guard（§8）。

### 3.3 控制面语义（全部有界）

| 方法 | 默认预算 | 过期语义 |
| --- | --- | --- |
| `snapshot(timeout=2.0)` | 参数 | 超时返回 `fidelity=unavailable/recovery=degraded` 的**无内容**快照；排队中过期的请求**不执行** |
| `reset_baseline()` | `control_timeout=2.0` | 超时返回明确 note；未发送则**不执行**；已发送（在途）则 note 声明 "unconfirmed（可能已执行）" |
| `restore_screen()` | `control_timeout=2.0` | 同上有界；失败返回 `False`（调用方走 fresh view） |
| `resize()` / `feed*` | 无等待 | 队列满：feed 显式拒绝并 sticky `feed_lag`；resize 计入 `control_queue_overflow` |

---

## 4. sidecar 线协议（`emulator_sidecar/sidecar.mjs`）

### 4.1 帧格式（stdin/stdout 双向、有界）

```
[u32le total_len][u32le header_len][header UTF-8 JSON][payload bytes]
total_len = header_len + payload_len
上限：total_len <= 8 MiB；header_len <= 64 KiB；header_len == 0、header_len > total_len
      或断帧一律拒绝（Python 抛 EmulatorProtocolError；sidecar 回 fatal 帧并退出）。
```

- 64 位量（`abs_start/abs_end/applied_bytes/processed_frontier/baseline_frontier/start_cursor`）
  **一律十进制字符串**；Node 侧用 `BigInt` 解析，禁用 JS number（>2^53 会静默舍入）。
  实测：`start_cursor = 2^53 + 987654` 往返精确（测试 `test_start_cursor_beyond_53_bits...`）。
- 身份 raw FILETIME 只在 Python 侧由内核读取（`GetProcessTimes`，同一 `Popen` 句柄），
  对外报告中以**十进制字符串**呈现（`diagnostics()["sidecar_filetime"]`）；不经过 JS。

### 4.2 命令（Python → sidecar）

| type | 字段 | 说明 |
| --- | --- | --- |
| `hello` | cols/rows/scrollback/start_cursor/max_pending_tail/max_reasons/max_rejected_ranges | 首帧；缺省拒收 |
| `feed` | op, abs_start, abs_end + payload(raw bytes) | 原始字节供料（无 base64；UTF-8 由 xterm 增量解码） |
| `resize` | op, rows, cols | 与 feed 同序 |
| `reset` | op | 清屏 + 定义新基线（§7.4） |
| `restore` | op + payload(serialized UTF-8) | 协议 A 状态重建（不推进源流账本） |
| `snapshot` / `barrier` | op | 原子返回 serialized + applied + 尺寸 + fidelity/recovery |
| `test_stall` | op, ms(≤10000) | **测试专用**确定性挂起钩子（生产路径不调用） |
| `test_inject_error` | op, target | **测试专用**故障注入钩子 |
| `shutdown` | op | 回 ack → dispose → 退出 |

### 4.3 响应（sidecar → Python）

- `ready`（含 pid、引擎版本、node 版本）、`applied`（每个 op 一条，含 `applied_bytes`/
  `processed_frontier`/`pending_tail_bytes`/`parser_dirty`）、`snapshot`（payload=serialized
  UTF-8）、`barrier`、`error {code, detail}`、`fatal {code, detail}`（进程将退出）。
- 死进程/卡住/EOF/异常全部**可见**：reader 收到 EOF/fatal/协议违例时把引擎置为
  sticky 不可用（`engine_dead`），后续 `snapshot` 返回 `unavailable/none` 并携带原因；
  控制等待者被唤醒（不挂死）。

---

## 5. 供料、快照与 cursor 语义

### 5.1 有界有序队列（Python 侧唯一队列）

- **全部 op**（feed/resize/snapshot/barrier/reset/restore/shutdown）进入同一条 FIFO；
  `max_queue_bytes = 4 MiB`、`max_queue_ops = 8192` **含控制命令**。
- feed 入队快速返回（不等 Node）；队列满 → 显式拒绝：
  `feed_lag=true`（latch）、拒绝区间有界记录（≤ `max_rejected_ranges=64` 条 + 溢出计数）、
  **不阻塞 PTY reader、不静默丢字节**（状态可见）。
- applier 严格单飞：一个 op 得到 sidecar 确认后才发下一个。控制命令的过期检查发生在
  "出队 + 发送"的同一临界区：**排队期间过期的控制命令绝不迟到执行**。
  （在极窄窗口内已写入管道的命令无法撤回；`note` 如实标注 unconfirmed。）

### 5.2 hold-back（pending tail）与协议 A

- sidecar 扫描每个 feed 的**合并流**，把未完成的 UTF-8/ESC/CSI/OSC/DCS 串留在
  `pending_tail`（默认上限 8 KiB；超限强制喂入并把 parser 置为 **sticky dirty**，
  直到显式 reset）。
- 因此快照的 `serialized` 状态与 `cursor`（applied）永远对齐在 clean 边界；
  `cursor` 之后的字节（含 pending tail）**没有**被喂给引擎。
- **协议 A（唯一推荐）**：
  1. `snap = emulator.snapshot()`；
  2. `restore_screen(snap.serialized_screen)` 重建屏幕；
  3. 从 `snap.cursor` 重拉**原始字节**并 `feed_at` 续流。
  **不预喂 pending tail**（它自然包含在重放流里）；混用两种游标会把尾部消费两次
  （探索 C13 已用字节计数证明）。`note` 中固定带有 `protocol=A(...)` 提示。

### 5.3 cursor 权威性

- `snapshot.cursor = applied_bytes`（sidecar 在 xterm write 回调完成后推进）：
  reader 入队 ≠ 已解析；**禁止**用 producer 的 `total_bytes` 冒充。
- 引擎死亡/关闭：`cursor` 保持最后确认值，但 `fidelity=unavailable/recovery=none`。
- 序列 gap/duplicate 供料：`cursor` 继续前进（诚实表示"已处理位置"），但
  `recovery=degraded`、`cursors_valid=false`（`diagnostics()` 可见），直到显式 reset
  清除旧基线前的记录。

---

## 6. 保守分类器（检测优先于声明）

sidecar 对完成的序列即时分类（**无事件缓冲、无饱和上限**——修复探索 B1），
凡未列入实测白名单一律降级：

| 序列 | 结果 |
| --- | --- |
| SGR / 光标运动 / 擦除 / 编辑 / 滚动 / 基础模式（10 个已测私有模式 + IRM） | 安全（不降级） |
| `CSI r`（DECSTBM 滚动区） | `DECSTBM_NOT_SERIALIZED`（实测缺口） |
| `?2026`（synchronized output） | `SYNCHRONIZED_OUTPUT_NOT_SERIALIZED`（实测缺口） |
| `?25`（DECTCEM 光标可见性）、DECSCUSR、OSC 标题/超链接、tab stops、DCS、未知私有模式（真实 ConPTY 已出现 `?9001`）、未知 CSI final/ESC、未知 OSC code、未知 mode | `*_UNVERIFIED` 降级 |
| 未完成的 parser 中间态（强制喂入后） | `PENDING_PARSER_STATE_UNSERIALIZED`（sticky 至 reset） |

不为了让业务"看起来 full"放宽白名单；`declareFeatures` 类"声明式保障"未进入生产协议。

---

## 7. 保真与恢复判定（fidelity / recovery）

### 7.1 判定表（Python 侧合并 sidecar 原因与自身账本，取更差者）

| 条件 | fidelity | recovery | 典型原因 |
| --- | --- | --- | --- |
| `reasons` 为空且无任何 sticky 状态 | `full` | `full` | 被测矩阵内（主/备屏、SGR、滚动、resize…） |
| 检测到缺陷/未验证序列、parser dirty、发生过 reset | `partial` | `partial` | `DECSTBM_*`、`?9001`、`BASELINE_RESET_FRESH_VIEW`… |
| feed 队列溢出（`feed_lag`）、序列 gap/duplicate、引擎 op 错误 sticky | `partial` | `degraded` | `FEED_QUEUE_OVERFLOW`、`SOURCE_GAP_STICKY`、`ENGINE_OP_ERROR_STICKY` |
| 引擎死亡/关闭、超时未取得快照、控制队列满 | `unavailable` | `none` / `degraded` | `eof`、`fatal:*`、`snapshot-timeout` |

- 只有 `full/full` 允许对用户表现为"完整恢复"；其余路径必须 fresh view/重打基线。
- `feed_lag` 恢复后**不自动**回到 full；需要显式 `reset_baseline()`，且 reset 之后
  仍因 `BASELINE_RESET_FRESH_VIEW` 保持 partial（**reset 不假恢复过去 full**）。

### 7.2 reset 语义（生产化修复）

- 新基线 = **命令实际执行位置**（sidecar 在有序队列中处理到该命令时的
  `processedFrontier`），**不是** producer 全局 frontier——排队在该命令之后的 feed
  不计入（否则快照 cursor 会跳过尚未应用的字节）。
- reset 清除**旧基线之前**的 gap/duplicate sticky（不再永久污染 cursor）；
  `feed_lag` 与检测原因保留（不假恢复）。
- 未确认（超时且未发送）的 reset 不执行；已发送未确认的在 note 中声明"可能已执行"。

---

## 8. 进程所有权与生命周期

- **出生即入组**：`CREATE_SUSPENDED | CREATE_NO_WINDOW` 创建 node → `AssignProcessToJobObject`
  （本 emulator 自持的 `JobObjectGuard`，`KILL_ON_JOB_CLOSE`）→ `NtResumeProcess`。
  实测证据：`guard.member_pids()` 含 sidecar PID（测试断言）。
- **runner 硬死不残留**：Job 句柄由 runner 进程持有；宿主进程死亡 → 内核关闭句柄 →
  sidecar（及 conhost）整树终止。测试以"子进程创建 emulator 后 `os._exit(7)`"实证
  （`test_runner_hard_death_kills_sidecar_via_job`）。
- **close 顺序**：优雅 `shutdown`（有界）→ 进程退出确认 → 关管道/join 线程 → 关 Job 句柄；
  优雅失败 → `identity.kill_verified`（同一 handle 核验）→ Job `terminate_tree`。
  **进程未确认退出前绝不关管道/Job**（否则 EOF/KILL_ON_CLOSE 会造成"伪收敛"）；
  任何一步失败 → `closed=False` 且资源保留，重试可收敛（测试覆盖 force 注入与
  guard.close 注入两条失败路径）。
- 重复 close 幂等（返回已收敛报告）。

---

## 9. 测试与证据

运行（隔离环境，无全局依赖变更）：

```powershell
E:/software/miniforge/python.exe -m pytest tests/test_terminal_emulator.py -q
```

矩阵（28 项）：构造/身份/大数 cursor、缺 Node/缺脚本不可用、帧边界、主屏与备用屏
headless↔headless 对拍（协议 A）、split UTF-8/CSI/OSC、pending tail cursor 边界、
resize 排序、browserless >256 KiB + OutputLog 游标二次驱逐、applied 滞后、
控制过期不执行、feed 快速有界、满队列 sticky 与有界诊断、控制队列溢出、
五个 MA 缺口的先失败后通过回归、duplicate 防双消费、sidecar 崩溃可见、
卡住/失败关闭重试、runner 硬死 Job 兜底、多实例 close 无泄漏。

证据目录 `audit/terminal/implementation/emulator/`：

- `pre_fix/`：基线复现（`pytest_pre_fix.log` = **10 failed / 18 passed**；含
  `sidecar.baseline.mjs`、`emulator.baseline.py`、`test_terminal_emulator.baseline.py` 快照）；
- `post_fix_pytest.log`：修复后 **28 passed**；
- `regression_all_terminal.log`：全终端测试回归；
- `pre_fix_log.json` / `post_fix_log.json`：失败/通过清单（机器可读）；
- `repro_ma_gaps.py`：五项缺口的独立复现脚本（可重复执行）。

---

## 10. 先失败后通过（探索残留 → 生产修复）

| # | MA 指出的探索残留 | 基线失败证据（`pre_fix/pytest_pre_fix.log`） | 修复 |
| --- | --- | --- | --- |
| B1 | `scanStream` 4096 事件上限掩盖后来的未知序列 | `test_unknown_sequence_after_event_saturation_is_detected`：`FULL`（`?7777` 未检测） | 逐序列即时分类，无事件缓冲/无上限 |
| B2 | rejected_ranges/诊断无界 | `test_feed_overflow_is_sticky_degraded_and_bounded`：200 次拒绝后 `rejected_ranges` 无界 | 有界表（64）+ 溢出计数（`overflow.rejected_range`）；reason 表与 op 错误同样有界 |
| B3 | feed 执行异常不降级 | `test_engine_op_error_is_sticky_degraded`：错误后快照仍 `FULL`/崩溃于 null cursor | sticky `ENGINE_OP_ERROR_STICKY`（partial/degraded）；失败 feed 推进 processed frontier（不造伪 gap）；null cursor 安全序列化 |
| B4 | reset 误取全局 frontier | `test_reset_baseline_uses_execution_position_not_future_frontier`：`baseline=3000`（应为 2000） | Python 采用 sidecar 执行位置；排队中的 future feed 不计入 |
| B5 | reset 后旧 gap 永久污染 cursor | `test_reset_clears_old_gap_and_never_fakes_full`：`cursor` 永久不可信 | reset 清除旧基线前 gap/duplicate；仍保持 partial（不假 full） |

另外两处**在修复过程中由测试抓出的真实缺陷**（诚实记录）：

1. sidecar `sendSnapshot/sendBarrier` 在 `gapSticky` 时对 `null` cursor 调用 `.toString()`
   → 快照崩溃并把引擎误标为不可用（基线 G5 用例的失败现场可见）；
2. 恢复端缺少"状态重建"入口：仅 `feed_at` 无法表达协议 A 第 2 步，
   基线对拍用例直接暴露（`'' == '$ echo hello…'`）。新增 `restore_screen()`（§3.2）。

---

## 11. 已知边界与未验证项（不得当作已解决）

1. **真实浏览器渲染保真**：全部对拍都是 headless↔headless；前端像素/选区/滚动一致性
   属 P3 门，本轮不声称。
2. **滚动深度**：`scrollback=1000` 行；更长历史的 serialized 体积与前端表现未测。
3. **自然供料停滞**：`feed_lag` 的注入路径已测（满队列）；"真实 PTY 突发 >4 MiB 且
   引擎跟得上"的长稳压测未做（spike §8.3 遗留项）。
4. **DECSTBM/?2026/?9001 等**：明确按 partial 处理，不在本轮承诺修复（序列化缺口属
   引擎能力，升级 addon 需重跑矩阵）。
5. **`allowProposedApi` 依赖**：addon-serialize 0.14.0 的既有要求；升级 addon/headless
   需重跑全矩阵（版本不符 sidecar 直接拒绝启动）。
6. **非 Windows**：不提供 sidecar 宿主（抛 `EmulatorUnavailableError`）。
7. **跨 sidecar 重启的恢复**：sidecar 崩溃后引擎状态不可恢复（`recovery=none`）；
   runner 侧若重建 emulator，只能从 OutputLog 重放（游标窗口内）。
8. **真实原语不足的替代**：未发现需要"假称"的原语；Job guard/身份核验均为真实调用，
   失败路径有测试。若未来某些 shell/安全上下文阻止 assign，属环境问题（构造期 fail-closed）。

---

## 12. 变更记录

- `2026-10-03` 首版：`emulator.py` + `emulator_sidecar/`（exact pin）+ 28 项测试 +
  证据；五项 MA 缺口先失败后通过（§10）；接口冻结面见 §3/§4（runner 桥接契约）。
