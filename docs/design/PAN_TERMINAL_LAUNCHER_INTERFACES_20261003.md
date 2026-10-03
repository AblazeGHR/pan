# Pan Terminal Launcher 最小接口与退出码/预算策略（2026-10-03）

- 任务：生产 launcher 的**引擎归属 / 启动失败清理 / 收尾 owner 保留重试闭环**。
  P2 服务 / Web / MCP / registry 不在本轮。
- 工作树：`D:/project/pan-worktrees/terminal-launcher-implement-20261003`
  （branch `implement/terminal-launcher-20261003`，seed `3f8ef045`）。
- 依据：`PAN_TERMINAL_COMPOSITION_BOUNDARIES_20261003.md`（I1：注入引擎由创建它的
  runtime 宿主负责；TerminalRunner 不隐式关闭借入对象）、`PAN_TERMINAL_COMPOSITION_R2_ACCEPTANCE_20261003.md`
  （三层收尾事实：引擎对象可重试 / 测试宿主未消费失败 owner / 生产 launcher 未实现）、
  独立审查 `326a646e`（v1 四态、v4 launcher 单 close 缺口）。
- 本文件是**接口成稿**（先于代码）；实现与实测计数在变更记录回填。
- 写范围：新增 `packages/core/terminal/launcher.py`、`tests/test_terminal_launcher.py`、
  本文件、`audit/terminal/implementation/launcher/**`。runner / client / contracts /
  backend / IPC / emulator / sidecar / registry / `__init__` / server 与既有测试只读。

## 1. 目标与边界

- 同一独立 runner 进程内：**创建并拥有**真实 `HeadlessEmulator`（node sidecar），
  借入 `TerminalRunner`（`emulator=` 注入；`backend.probe` 为默认身份核验，不注入
  `identity_probe`；输出经 `RunnerEmulatorBridge` 的 `feed_at` 桥接）。
- 不依赖 Pan 服务生存（宿主与引擎同进程，符合 detach 语义的所有权前提）。
- 入口沿用 runner 同款四门（`TerminalRunner.run()` 内部）：terminal_id 校验 +
  secret 路径 / DPAPI 读取 / HMAC 双向认证 / 原子 spawn（runner 出生持 PTY + guard）。
  **launcher 自身不接触 token**；argv 只有 id 与 secret 路径（无 token、无 env 注入）。
- 明确不做：P2 服务接线、浏览器、registry、MCP；不倒写被接受测试宿主的历史。

## 2. 进程模型与组装顺序

```
python -m packages.core.terminal.launcher \
    --terminal-id <term_...> --secret-file <root>/secrets/<id>.secret \
    [--rows 24] [--cols 80]
```

1. `HeadlessEmulator(cols, rows, ...)` —— 引擎构造（spawn node sidecar + 握手）。
2. `TerminalRunner(terminal_id, secret_file, emulator=engine, rows=, cols=)` —— 借入。
3. `runner.run()` —— 四门 / PTY / IPC 服务循环（语义与 runner 入口一致）。
4. `finally`：引擎收尾（§4）——宿主责任；`TerminalRunner` 不关闭注入对象。
5. 合并退出码（§3）+ 落盘状态（§6）+ stderr 单行公告。

### 2.1 各构造阶段 owner（复用 emulator 契约）

| 阶段 | owner | launcher 行为 |
| --- | --- | --- |
| 引擎 spawn 前失败（平台/node/sidecar 缺失） | `EmulatorStartupError.owner is None` | 无自有资源可清；记录 `no-owner`，不伪造消费 |
| 引擎 spawn 后失败（ready 前 EOF / fatal 帧 / 握手超时） | `owner`（proc/guard/线程引用） | **实际消费**：`retry_cleanup()` 循环（同 owner，受总 deadline） |
| 引擎构造成功 | 引擎对象本身（close 幂等可重试） | 收尾时单飞 worker 消费（§4） |

## 3. 退出码

| code | 含义 | 来源 |
| --- | --- | --- |
| 0 | runner 正常退出且引擎收尾**已确证收敛** | 透传 |
| 2 | 用法错误 | 与 runner 同（argparse） |
| 3 / 4 / 6 / 7 | runner 的 cleanup-failed / bootstrap-failed / cleanup-unproven / accept-failed | **透传**（引擎收尾收敛时） |
| 5 | launcher 兜底（runner.run 抛错 / 自身内部错误） | launcher |
| 6 | **清理未证明**：引擎收尾未收敛，或引擎启动失败后 owner 清理未收敛 | launcher（与 runner 值一致） |
| 8 | **引擎启动失败**且 owner 清理**已确证收敛** | launcher 新增 |

- 覆盖规则：引擎清理未收敛 → 一律 6（原因写状态文件；不因 runner 原码而被掩盖）。
  其余情况 runner 的码原样透传（3/4/5/6/7）。
- stderr 公告（脱敏、静态串）：
  `pan-terminal-launcher: exit code=N reason=<static> engine-cleanup=<converged|unproven>`

## 4. 引擎收尾策略（总预算 / 单飞 worker / 同 owner 重试）

- 总预算 `engine_total_budget = 20s`：**从收尾入口计时**，包含全部等待（worker 等待、
  重试间隔、`close()` 内部等锁）与全部尝试；不重置、不静默延长。
- 单次尝试 `engine_close_attempt_budget = 8s`：作为 `emulator.close(timeout=…)` 的
  单次调用预算（emulator 契约：该预算含等 `_close_lock` 的时间）。
- 重试间隔 `engine_retry_interval = 0.5s`；重试是**同 owner**（同一引擎对象；
  `close()` 幂等、失败保留资源可重试）。
- **单飞 worker**：同一时刻至多一个 `close()` 调用在途。若上一次调用超预算未返回：
  不另起第二个调用（不叠加）、不裸关资源（不 TerminateProcess / 不强杀 sidecar）、
  继续等待**同一** worker（直到总预算耗尽），如实记录 `in_flight`。
- 结束判定：`closed=True` 才叫收敛。`closed=false` / 抛异常 → 记录 `error_type`
  后按剩余预算重试；预算耗尽 → **非零退出（6）** + 状态文件（保留资源、原因类型、
  尝试次数、是否 in-flight）。
- 口径声明：本预算为**调用方侧有界等待**，不是 OS 原语硬 SLA；进程退出关闭 Job
  guard 句柄导致的内核清树**不算清理已证明**（只按已测布局陈述，不泛化）。
- 失败 owner 的对象引用**仅本进程内有效**；状态文件是事实记录，**不是跨进程重试入口**。

## 5. 启动失败清理（owner 实际消费）

`EmulatorStartupError`：
- `owner=None` → 记 `no-owner` + 引擎残留事实（`residual`），退出码 8（无清理缺口）。
- `owner!=None` → `owner.retry_cleanup(timeout=剩余预算切片)` 循环：
  - `closed=True` → 收敛：退出码 **8** + 状态文件（attempts / residual）。
  - 未收敛（`retryable`/超时）且总预算未耗尽 → 有限间隔后重试（同 owner；owner 内部
    已串行化，不并发重叠）。
  - 总预算耗尽 → 退出码 **6** + 状态文件（`retained`、`last_error_type`、attempts、
    `deadline_exceeded` 事实）。
- `residual` 记录（emulator 契约字段）：`pid` / `identity_filetime`（raw FILETIME
  字符串）/ `process_exited` / `job_verified` / `cleanup_errors`（类型名列表）等；
  原样透传到状态文件（不追加解码、不猜结构）。

## 6. 状态文件（两结局显式、脱敏、原子）

- 路径：`<terminals_root>/launcher-status/<terminal_id>.json`
  （与 runner 的 `runner-status/` 平行；`<terminals_root>` = secret 文件的祖父目录）。
- 写前 `validate_terminal_id`；非法 id **零派生文件**（只记 `status-path-rejected`，不回显 id）。
- 唯一自有 tmp（`<name>.<hex>.tmp`）+ 原子 `os.replace`；写串行（进程内锁）；
  失败只记类型名、**不抛出、不假成功**（退出码不因写失败改变；stderr 记 `status-write-failed`）。
- 字段（schema_version=1）：

```json
{
  "schema_version": 1,
  "terminal_id": "term_...",
  "phase": "finished | engine-startup-failed | cleanup-unproven | internal",
  "exit_code": 0,
  "reason": "<静态串>",
  "launcher_identity": {"pid": 1234, "process_created_at_filetime": "1334...（十进制字符串）"},
  "engine": {
    "created": true,
    "cleanup": {"converged": true, "attempts": 1, "last_error_type": null,
                 "retained": [], "in_flight": false, "seconds": 0.3,
                 "outcome": "closed"},
    "startup": {"reason": "ready-eof", "owner": "consumed", "converged": true,
                 "attempts": 2, "residual": {"pid": 4321, "identity_filetime": "..."}}
  },
  "authority": "launcher-status 是事实记录；对象引用不可跨进程重试；Job 内核退出不代表清理已证明",
  "updated_at": 0.0
}
```

- 脱敏：只允许类型名 / 静态串 / 结构化字段；**异常消息文本不落盘**；
  token / secret 不出现（另有 token 哨兵门控）。
- `launcher_identity` 来自 `current_process_identity()` 的**真实** pid + raw FILETIME
  十进制字符串；未能核验时明确 `null`（unknown，不造身份；N3 口径）。

## 7. 类与函数（最小接口）

```python
DEFAULT_ROWS = 24; DEFAULT_COLS = 80
LAUNCHER_EXIT_INTERNAL = 5
LAUNCHER_EXIT_ENGINE_STARTUP_FAILED = 8
LAUNCHER_EXIT_CLEANUP_UNPROVEN = 6          # 与 runner 同值
ENGINE_TOTAL_BUDGET_SECONDS = 20.0
ENGINE_CLOSE_ATTEMPT_BUDGET_SECONDS = 8.0
ENGINE_CLEANUP_RETRY_INTERVAL_SECONDS = 0.5

class TerminalLauncher:
    def __init__(self, terminal_id, secret_file, *, rows=24, cols=80,
                 engine_total_budget=..., engine_close_attempt_budget=...,
                 engine_cleanup_retry_interval=...,
                 status_dir: Path | None = None,
                 emulator_factory=None,   # 仅测试/诊断（默认 HeadlessEmulator）
                 runner_factory=None) -> None   # 仅测试/诊断（默认 TerminalRunner）
    def run(self) -> int                     # 主流程（§2），返回合并退出码
    @property engine_cleanup(self) -> dict    # 收尾报告（供审计/测试）
    @property startup_report(self) -> dict | None   # 启动失败 owner 消费报告
    @property events(self) -> tuple[tuple[str, str | None], ...]  # 脱敏事件轨迹
    @property exit_reason(self) -> str | None

def main(argv: Sequence[str] | None = None) -> int
```

- `emulator_factory/runner_factory` 注入仅测试/诊断（与 runner 的 `write_hook` 同惯例）；
  生产 `main()` 不注入。
- launcher 不修改 runner/emulator 任何共享签名。

## 8. 明确不承诺（未验收门维持）

- 不接 P2 / Web / MCP / registry；Ctrl-C 未验收；真实 durable detach 在 ambient Job
  下维持**拒绝**（本机不可 breakaway，不做逃脱实验）。
- 浏览器渲染/fit、provider/账号/网络服务、跨用户/主机、长稳/慢客户端背压、POSIX、
  跨 sidecar 重启恢复未验收。
- write budget 是发起取消的截止、**非硬 SLA**；真实会话 partial 是常态（未验证 VT
  序列不升级）；F5 确认字段为保守近似、**无历史世代原子绑定**。
- Job 内核退出兜底只按已测布局（launcher 硬死 → 整树消亡）陈述，不泛化。

## 9. 变更记录

- `2026-10-03` 首稿（先于代码）：接口/退出码/预算策略成稿。
- `2026-10-03` 实现与实测回填：`launcher.py` + 17 项测试（12 注入门控 + 5 真机）；
  **launcher 直连 17/17、uv 17/17；13 组合相邻回归双环境各 13/13（均 rc 0）**；
  残留扫描 0；证据见 `audit/terminal/implementation/launcher/**`（UTF-8 `.txt` + JSON +
  source blob 锚定）。uv 下 venv python 为 shim（Popen pid ≠ 真解释器 pid）：身份权威
  = bootstrap 自证 + status 交叉核验（详见该目录 README）。
