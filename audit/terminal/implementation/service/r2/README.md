# P2 第一批 r2 返工：TerminalService F1–F10 闭环证据（service/r2）

本目录只放 **r2 返工**的证据。首版证据 `audit/terminal/implementation/service/**`
与独立审查证据 `audit/terminal/implementation/service-review/**` **均未修改、未覆盖**。

- 工作树：`D:/project/pan-worktrees/terminal-service-implement-20261003`
- 分支：`implement/terminal-service-20261003`
- 返工起点（被审查提交）：`cde2dbd8`；审查：`d7905408`（判**返工**）
- 本轮写范围：**仅** `packages/core/terminal/service.py`、
  `tests/test_terminal_service_rework.py`（新增）、本文档、本目录。
  **launcher 与所有共享模块只读**；原 `tests/test_terminal_service.py` **未修改**
  （由独立 ds 卫生任务占用）；未 cherry-pick 其在途改动。

## 1. 逐项闭环映射（F1–F10）

| # | 审查结论 | r2 修订（`service.py`） | 回归用例（`test_terminal_service_rework.py`） |
| --- | --- | --- | --- |
| **F1** | 活状态 reconcile 把"我们派生的进程退出"当整树终止证明并删秘密；默认只比 FILETIME | `_reconcile_live` **以 runner 身份三态为门**：仅 `dead-confirmed` 才写 `exited`/删秘密；ALIVE/UNKNOWN/身份不符 → 保秘密+保记录。`_identity_evidence` **同时核对 PID + raw FILETIME**（新增 `pid-mismatch`）。`exit 6` 引擎未确证不算成功（`engine_converged` 必为真 bool）。无句柄时改用身份三态且**不造证明** | `f1_reconcile_live_alive_identity_keeps_secret_and_record`、`f1_reconcile_live_unknown_identity_keeps_secret`、`f1_reconcile_live_dead_identity_is_the_only_delete_path`、`f1_identity_evidence_requires_pid_and_filetime_match`（含**错 PID + 同 FILETIME** 负例）、`f1_launcher_exit6_engine_unproven_is_not_clean_success`、`f1_reconcile_never_terminates_unknown_pid` |
| **F2** | 瞬时失败 stop 被永久缓存，`close` 不可收敛 | stop **有界幂等重发**（`MAX_STOP_RESENDS=2`）：在途**单飞复用**；已完成未确认（`closing`/异常）才重发；**已确认不机械重复**；仅 worker **确已结束**才 `reset()` | `f2_stop_returning_closing_can_be_retried_boundedly`、`f2_stop_raising_exception_can_be_retried`、`f2_already_confirmed_success_is_not_resent`、`f2_inflight_stop_is_single_flight` |
| **F3** | 跨进程遗留 managed/cleanup-failed 无重发 stop 入口 | 新增 `_retry_persisted_stop`：秘密在位 + 身份 `alive`（pid+FILETIME）才动手；有界死期等待；端点核验重连后发 stop，**不建心跳**（不通过续约复活）；成功仍需三项证明；每记录本实例只试一次 | `f3_persisted_cleanup_failed_gets_bounded_stop_reentry`、`f3_persisted_unattributable_never_touches`、`f3_no_selfspawn_handle_means_no_fabricated_proof`、`f3_does_not_revive_old_managed_by_renewal`、`f3_reconcile_survives_endpoint_refusal_without_termination` |
| **F4** | reconnect 覆盖 client/心跳，旧资源泄漏（1→2→3→4 线程） | `_reconnect` **先复用或真实回收**：已有可用连接则复用；否则先停旧心跳、再释放旧连接，然后才建新连接 | `f4_reconnect_releases_old_client_and_stops_old_heartbeat` |
| **F5** | shutdown 尾部无条件 release，未收敛终端此后永不可收敛 | **只**释放已收敛/kept-detached/已终态的连接；未收敛项**保留** `client` 与 `close_call`（`retained_for_retry`），只停心跳线程 | `f5_unconfirmed_terminal_keeps_client_for_retry`、`f5_inflight_close_worker_survives_shutdown_release` |
| **F6** | 预算不含锁等待（`max(0.1)`抬高 0 预算；shutdown 谎报 within_budget） | `state.lock` 改**有界** `acquire(timeout=…)` 并纳入 deadline；`budget=0` 不抬高；停心跳按剩余预算；新增 `elapsed_seconds`，`elapsed_within_budget` 如实。**锁序** `_admission_lock`→`_global_lock`→`state.lock`；其余路径不取 `state.lock`（避免"等锁超时后再取同一把锁"二次挂死） | `f6_close_budget_includes_state_lock_wait`、`f6_shutdown_budget_includes_lock_and_reports_honestly`、`f6_zero_budget_is_not_inflated` |
| **F7** | 预算耗尽 `break` 后未处理记录不计入 unconfirmed（`secrets_retained` 假报） | 预算耗尽时把剩余记录**全部**列入 `unconfirmed` 并单列 `skipped`；`secrets_retained = bool(unconfirmed ∪ skipped)` | `f7_budget_exhausted_records_all_listed_as_unconfirmed`（**确定性锚定**：多条记录 + 阻塞 stop，**不推断**） |
| **F8** | 未证明路径 close 后 input 仍放行（内存态陈旧） | `_lease_lookup` 读**磁盘 registry**（权威）；`_close_state`/`_mark_unproven` 后**同步内存态**（`_sync_state_record`） | `f8_close_admission_gate_blocks_input_before_rpc`、`f8_memory_record_synced_after_close` |
| **F9** | shutdown 后 create 无准入门 | `shutdown` **先关准入**（`_admission_lock` 内置 `_closing_down`），与并发 create **线性化**；新增 `ServiceClosingDown` | `f9_create_rejected_after_shutdown`、`f9_create_and_shutdown_linearized` |
| **F10** | close reason 不区分缺哪一项证明 | reason 改**内部静态分类**：`runner-stop-unconfirmed` / `engine-cleanup-unproven` / `process-still-running` / `close-in-flight` / `close-lock-wait-timeout` / `cleanup-unconfirmed`；不再回显调用方 reason；无自由文本 | `f10_close_reason_distinguishes_missing_proof`、`f10_no_free_text_leak_in_close_error` |
| **F12** | 真实心跳用例时序flake | 产品实现无需改动；本轮回归改**有界等待** | `f12_heartbeat_progress_uses_bounded_wait` |

## 2. 先失败 → 后通过

- **先失败**：`evidence/prefix_rework_against_cde.txt` —— 把新增回归套件跑在
  `git archive cde2dbd8` 的**只读副本**（无 `.git`，`C:/Users/14709/AppData/Local/Temp/cde-ro`）上，
  得 **21 failed / 8 passed（rc=1）**：F1–F10 逐项复现。F6/F7 在此为**确定性复现**
  （F7 用"多条记录 + 阻塞 stop"构造，不依赖推断）。
- **后通过**：修后同套件 **31 passed（rc=0）**（新增 2 项真实层）。见下表。

## 3. 实测（本轮亲自执行；日志 `-o addopts= -q`）

| # | 项目 | 命令 | 结果 | 日志 |
| --- | --- | --- | --- | --- |
| 1 | r2 回归套件（直连） | `python -m pytest tests/test_terminal_service_rework.py -o addopts= -q -p no:cacheprovider --timeout=200` | **31 passed** rc=0 | `evidence/postfix_rework_direct.txt` |
| 2 | 原 41 套件（直连，**未修改**） | `python -m pytest tests/test_terminal_service.py -o addopts= -q -p no:cacheprovider --timeout=200` | **40 passed / 1 failed** rc=1（**已知冲突**，见 §4） | `evidence/postfix_original41_direct.txt` |
| 3 | 两套合并（uv 隔离） | `uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout --with pyte==0.8.2 -- python -m pytest tests/test_terminal_service_rework.py tests/test_terminal_service.py -o addopts= -q -p no:cacheprovider --timeout=200` | **71 passed / 1 failed** rc=1（同§4 冲突） | `evidence/postfix_uv_both.txt` |
| 4 | 相邻 registry+lease（一次） | `python -m pytest tests/test_terminal_registry.py tests/test_terminal_lease.py -o addopts= -q -p no:cacheprovider` | **40 passed** rc=0 | `evidence/postfix_adjacent_registry_lease.txt` |
| 5 | 源码锚定（LF 归一化，F11 口径） | 见 `evidence/source_anchoring.txt` | 6 份文件，全部 64 位 hex | `evidence/source_anchoring.txt` |

**真实层**（本轮新增 2 项，含真ConPTY/Job/DPAPI/管道/headless 引擎）：

- `test_real_restart_recovery_converges_via_reconcile`：宿主"重启"（丢弃内存态）后，
  新实例 reconcile 把遗留记录收敛；秘密要么保留、要么凭 `verified_exit` 删除。
- `test_real_close_failure_then_retry_converges`：极小 `stop_confirm` 令首个 close
  必"未证明"（停心跳/断连保留秘密），随后正常预算**重试** → 三项证明齐备 → `exited` + 删秘密。

**未跑**（按任务边界）：全库、旧 core/backend/IPC/emulator/组合、launcher 旧 83 全量
与真机、浏览器/provider/长稳、Ctrl-C、真实 durable detach、跨用户/主机/POSIX。
因此 launcher 16 项定向与真机对照**本轮未复核**（r2 未改launcher，只读）。

## 4. 与原 41 套件的**已知冲突**（报告，不擅自改）

`tests/test_terminal_service.py::test_reconcile_dead_confirmed_with_matching_identity`
在本轮 **1 failed**：该用例用 `_FakeProbe(DEAD, filetime)` 构造"已死"身份，而
`_FakeProbe` 把 `ProcessIdentity.pid` **硬编码为 1**（见原文件 L139-148）。r2 的 F1 要求
身份**同时**匹配 PID 与 FILETIME，故该 stub 的 `pid=1` 与记录的 `pid≈4000x` 不符 →
正确判为 `unattributable`（而非 `dead-confirmed`），断言 `dead-confirmed==1` 失败。

- 这是**旧测试替身保真度不足**与**r2 更严身份门**的冲突，**不是**产品缺陷：
  同一场景在 r2 回归里以 `ProcessIdentity(pid=真实pid, filetime)` 表达即通过
  （`f1_reconcile_live_dead_identity_is_the_only_delete_path`）。
- 原文件由独立 ds 卫生任务占用，本轮**禁止修改**、**不降能力**、**不篡改断言**，
  故如实报告该 1 failed。

## 5. F11 证据口径（本轮一并纠正）

首版 README 的哈希标注不一致（`launcher.py` 记 CRLF 工作树字节、
`test_terminal_launcher.py` 记 63 位截断串）。r2 的 `evidence/source_anchoring.txt`
对 6 份文件同时给出**工作树 CRLF** 与 **LF 归一化**两个 sha256（均 64 位），可复现。
本目录哈希以 **LF 归一化**为锚定口径。

## 6. 资源清理与安全

- 真实层清理只对**自有**资源做**同 handle 核验**（raw FILETIME + Wait），
  经 `win_pipe.terminate_verified_process`；不按 PID 单值/进程名/命令行广杀。
- 异常/超时路径同样只经同 handle 核验清自有资源；`test_f5_inflight_*` 用有界
  `join` 并 `finally` 释放 Event，避免挂死。
- 未启动既有 Pan/完整 Web 服务；未用 8768；未创建账号；未开网络监听；未 stash/reset/
  push/merge/restart；未派子代理。
- `emulator_sidecar/node_modules/` 仍被 gitignore（**不是** porcelain dirty），保留不提交。

## 7. 未验收 / 未验证

Ctrl-C；真实 durable detach（ambient 下维持拒绝）；浏览器渲染/fit；Web 鉴权
（Origin/CSRF/MCP caller gate）；provider/账号/网络服务；跨用户/跨主机；POSIX；
长稳/慢客户端背压；跨 sidecar 重启恢复；吞吐外推。预算是**调用方侧有界等待，非 OS
硬 SLA**。预算是真实会话 `partial` 为常态；F5 确认字段为保守近似、无历史世代原子绑定。
Job 内核退出兜底只按已测布局陈述，不泛化。容量准入仍只是**单服务实例内**硬约束。
