# P2 r3 窄修：MA 已决修法的执行证据（service/r3）

本目录只放**r3 窄修**证据。`service/`（首版）、`service/r2/`、`service-review/`、
`service-hygiene/`（卫生 `7f3ed5ce` 已冻结）**均未修改、未覆盖**。

- 工作树 / 分支：`D:/project/pan-worktrees/terminal-service-implement-20261003` /
  `implement/terminal-service-20261003`
- 起点（已核验 clean）：`de90b1ef`
- 性质：**执行 MA 已决定的修法**，不扩架构、不做全轮审查。
- 写范围：`packages/core/terminal/service.py`、service 接口文档、
  `tests/test_terminal_service_rework.py`、本目录；**额外授权**：
  `tests/test_terminal_service.py` 的 `_FakeProbe` 定义 + 必要 PID 传参调用点
  （**未改任何断言、未触碰心跳卫生区**）。launcher 与所有共享模块**只读**。
- 卫生提交 `7f3ed5ce`（另一分支 `implement/terminal-service-hygiene-ds-20261003`）
  **只读查阅、未 cherry-pick**；整合由 MA 之后进行。

## 1. 四项修法与门控映射

| # | MA 已决修法 | 实现（`service.py`） | 门控（`test_terminal_service_rework.py`） |
| --- | --- | --- | --- |
| 1 | 缺 `observed_pid` 必须 `unattributable`；PID/FT 非整数或转换失败 → 静态分类，不抛不伪造 | `_identity_evidence`：观测身份缺失 → `identity-missing`；`observed_pid is None` → `pid-missing`；`observed_ft is None` → `filetime-missing`；`int()` 失败 → `pid-invalid` / `filetime-invalid`（**全部 fail-closed**） | `r3_identity_missing_pid_with_matching_filetime_is_unattributable`、`r3_identity_non_integer_pid_or_filetime_is_static_unknown`、`r3_identity_true_pid_and_filetime_is_positive_control`、`r3_identity_wrong_pid_with_matching_filetime_rejected` |
| 2 | `reset()` 返回 False 不得读未定义变量；同终端重发决定必须串行 | `reset()` 为假 → **保留/重读原调用**结果（记 `close-stop-resend-refused`），不假收敛；新增**独立 `close_op_lock`**（`threading.Lock`）串行化"判定+重发"，锁等待计入本次 deadline，且**不包裹**阻塞 `call.run` | `r3_close_reset_false_keeps_original_stop_result`、`r3_close_two_callers_resend_is_serialized`（断言 `max concurrent stop == 1` 且真成功被消费） |
| 3 | shutdown 入口起 deadline；准入关门不因取锁超时丢失；如实报告 | 入口即 `started`/deadline；先置**无锁 Event** 再**有界**取准入锁（超时也继续收敛并记事件）；`_admit` 复查"标志 **or** Event"；`elapsed_within_budget` 仅容忍 **1ms** 浮点噪声 | `r3_shutdown_admission_lock_wait_is_bounded_and_reported`、`r3_shutdown_budget_zero_is_not_inflated`、`r3_create_after_shutdown_always_rejected_even_racing` |
| 4 | 端点短暂拒绝后可恢复；连接失败局部真实 release；不造句柄/不启动 managed 心跳；保可重试 state | `_retry_persisted_stop` 去一次性闩锁；端点连接抽为 `_attach_persisted_stop_client`（失败**局部真实 `release_connection`** + 清 `client/attached`）；`_reconcile_live` 对"无句柄且无 client"的 cleanup-failed **重新路由**回该路径 | `r3_persisted_stop_endpoint_transient_refusal_recovers`（含 **attach 计数**与 **stop 计数**锚点）、r3_persisted_stop_attach_count_and_no_fake_handle` |

**明确不做**：不声称普通 registry I/O 是 OS 硬 SLA；不扩共享协议；不改 launcher。

## 2. 先失败 → 后通过

- **先失败**：`evidence/prefix_r3_gates_against_de90.txt` —— 把本轮 11 项定向门跑在
  `git archive de90b1ef` 的**只读副本**（无 `.git`，`C:/Users/14709/AppData/Local/Temp/de90-ro`），
  得 **6 failed / 5 passed（rc=1）**，逐项复现 MA 描述的确定性事实：

  | 门 | 修前实测 |
  | --- | --- |
  | 1a | 缺 PID + FT 匹配 + DEAD → 判 `identity-mismatch`（被拒但归类不准；修复后为 `pid-missing`） |
  | 1b | `TypeError: int() argument must be ... not 'object'` —— **转换失败直接抛异常** |
  | 2a | `UnboundLocalError: cannot access local variable 'finished2'`（与 MA 门控一致） |
  | 2b | 两 caller 结果 `['unconfirmed','unconfirmed']` —— 真成功未被消费 |
  | 3a | `elapsed 不得谎报 0（实测 0.0）` —— 与 MA 实测一致（0.406s 却报0.0/within=True） |
  | 4a | `第 2 轮应重新 attach（实际 1 次）` —— 一次闩锁后再不attach |

- **后通过**：同套11 项 **11 passed（rc=0）**。

## 3. 实测（本轮亲自执行；`-o addopts= -q`）

| # | 项目 | 命令 | 结果 | 日志 |
| --- | --- | --- | --- | --- |
| 1 | 纯逻辑定向，**两套**（直连） | `python -m pytest tests/test_terminal_service_rework.py tests/test_terminal_service.py -o addopts= -q -p no:cacheprovider --timeout=200 -k "not real"` | **77 passed / 6 deselected** rc=0 | `evidence/postfix_purelogic_direct.txt` |
| 2 | 纯逻辑定向，**两套**（uv 隔离） | `uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout --with pyte==0.8.2 -- python -m pytest tests/test_terminal_service_rework.py tests/test_terminal_service.py -o addopts= -q -p no:cacheprovider --timeout=200 -k "not real"` | **77 passed / 6 deselected** rc=0 | `evidence/postfix_purelogic_uv.txt` |
| 3 | 受影响**真实层**两项（各一次） | `python -m pytest tests/test_terminal_service_rework.py -o addopts= -q -p no:cacheprovider --timeout=200 -k "real_restart or real_close_failure"` | **2 passed** rc=0 | `evidence/postfix_real_affected.txt` |

**r2 遗留冲突已消除**：`test_reconcile_dead_confirmed_with_matching_identity`（此前因
F1 PID 门 vs 旧 `_FakeProbe` 硬编码 `pid=1` 而 failed）**本轮通过** —— 按授权给
`_FakeProbe` 增加显式 `pid` 参数并在"匹配"语义处传记录真实 PID；**未改任何断言**。

**替身保真修正**（MA 要求）：r2 回归中两处用错 PID 探针导致**绕过目标分支**的用例
（`f3_does_not_revive_old_managed_by_renewal`、`f3_reconcile_survives_endpoint_refusal_...`）
已改为记录真实 PID + FILETIME，使其真正命中目标分支。

**未跑**（按任务边界）：41/31 全套真机、相邻 registry/lease 40、launcher、core、
全库、浏览器/provider/长稳、Ctrl-C、真实 durable detach。

## 4. 源码锚定（LF 归一化 blob，64 位 hex）

见 `evidence/source_anchoring.txt`（4 份文件，全部 hexlen=64）。口径为 **LF 归一化
git blob**（`core.autocrlf=true` 下工作树为 CRLF），沿用 r2 确立的 F11 修正口径。

## 5. 资源清理与安全

- 真实层清理只对**自有**资源做**同 handle 核验**（raw FILETIME + Wait），经
  `win_pipe.terminate_verified_process`；不按 PID 单值/进程名/命令行广杀。
- 事后只读复查：**存活遗留自有终端 0**。
- 未启动既有 Pan/完整 Web 服务；未用 8768；未创建账号；未开网络监听；未 stash/reset/
  push/merge/restart；未派子代理；未 cherry-pick 卫生提交。
- `emulator_sidecar/node_modules/` 被 gitignore（**不是** porcelain dirty），保留不提交。

## 6. 未验收 / 未验证

Ctrl-C；真实 durable detach（ambient 下维持拒绝）；浏览器渲染/fit；Web 鉴权
（Origin/CSRF/MCP caller gate）；provider/账号/网络服务；跨用户/跨主机；POSIX；
长稳/慢客户端背压；跨 sidecar 重启恢复。预算是**调用方侧有界等待，非 OS 硬 SLA**
（**不**把普通 registry I/O 声称为硬 SLA）。真实会话 `partial` 为常态；F5 确认字段
为保守近似、无历史世代原子绑定。Job 内核退出兜底只按已测布局陈述，不泛化。
容量准入仍只是**单服务实例内**硬约束。
