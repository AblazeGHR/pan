# runner r2 返工证据（闭环独立审查 `a12a6bd6`）

- 被审基线：`d48f967e`（报告 `docs/design/PAN_TERMINAL_RUNNER_REVIEW_20261003.md`
  @ 审查树 `terminal-runner-review-20261003`，判定 **返工 Restricted**）。
- 本目录：**r2 返工** 的新证据（UTF-8 `.txt` + JSON；不覆盖 d48 的 `evidence/final/`）。
- 复跑入口：
  `E:/software/miniforge/python.exe audit/terminal/implementation/runner/collect_r2_evidence.py`

## 1. 先失败（d48 固定副本，只读归档）

`pre_fix_gates.py` 用 `git archive d48f967e packages` 导出**固定副本**（不改任何工作树），
在进程内注入替身上复现 9 项缺陷（`injection_only=true`；机制等价于审查 neg01/02/03/04/06）：

| 场景 | d48 实测 | r2 期望 | 缺陷 |
| --- | --- | --- | --- |
| R1-a close 在途 + lease 丢失 | watchdog 空转跳过；迟到成功未消费；`exit=None`、state=`closing` | 消费同一 worker → shutdown、exit 0 | ✅ 复现 |
| R1-b close 失败 + lease 丢失 | 无自动重试；`close_calls=["explicit-close"]`、`exit=None` | 有界重试（不叠加）→ 耗尽 exit 3 | ✅ 复现 |
| R2-a expiry 先取得关闭权 | detach 返回 `rejected`（无显式拒绝语义） | `detach-refused`（lease-close-in-progress） | ✅ 复现 |
| R2-b detach 先完成 | `detached=True` 后仍 `close_calls=["lease-expired"]`（整树被杀） | 跳过（`close_calls=[]`） | ✅ 复现（铁证） |
| R2-c 迟到心跳 | 续约后旧 expiry 仍关闭（无世代复核） | 跳过（旧 expiry 作废） | ✅ 复现 |
| R3 启动清理双失败 | `_abort_backend` 返回 None、无 owner 保留 | 报告 + `retained=['backend']` + 重试 + 退出码 4/6 区分 | ✅ 复现 |
| O1 pipe `converged=False` | 报告被丢弃；attempts=1；无引用 | 保存报告 + 保引用 + attempts=2 + 可重试 | ✅ 复现 |
| O3 连接 join | 3 阻塞线程串行 `join(2s)` = **6.02s**；无回收方法 | 共享总 deadline（≤3s）+ 未收敛报告 + 回收 | ✅ 复现 |
| O4 detail 超界 | `_payload` 截断到 3800 → **非法 JSON**；`_detail_with` 20047 字符超界 | 字段级缩减：均 ≤3800 且可解析 | ✅ 复现 |

`pre_fix_gates.json` 含逐场景实测值与 `all_defects_reproduced=true`。

## 2. 后通过（修复后，直连与 uv 分列，不混合计数）

| # | 环境 | 命令（argv 见 `uv_env.json`） | 结果 | 日志 |
| --- | --- | --- | --- | --- |
| 1 | 直连 `E:/software/miniforge/python.exe` | `pytest tests/test_terminal_runner.py -q -rA` | **31 passed / exit 0**（82.95 s） | `pytest_direct.txt` |
| 2 | uv 0.9.14 隔离（`--no-project` + minimal-requirements + pytest/pytest-timeout） | 同文件 | **31 passed / exit 0**（84.74 s） | `pytest_uv.txt` |
| 3 | uv + `pyte==0.8.2` 隔离（单列） | core 六文件 + broadcast | **137 passed / exit 0**（9.42 s；72+65 点） | `core137_uv_pytest.txt` |

- 31 = 原 19 项 + r2 新增 12 项；每用例机器可读证据在 `evidence_direct/`（51 份）与
  `evidence_uv/`。
- 资源清理（直连全量夹具汇总）：21 个用例带 cleanup 轨迹、20 个句柄、
  **0 个在清理时仍存活、0 个需要夹具强杀**；残留扫描（只读 PowerShell）**0**。
- 源文件锚定：`source_blobs.json`（runner.py / runner_client.py / 测试 / 文档 /
  本 README 的 `git hash-object`，对应提交内跑）；`uv_env.json` 记录 uv 版本与三条命令行。
- 未做（按要求）：全库套件、旧 backend/IPC 套件、真实 emulator/provider/browser、
  30 次 spawn 压力；未见主线/PR/push/restart。

## 3. 返工闭环 ↔ 测试/证据映射

| 审查项 | r2 实现点 | r2 测试 |
| --- | --- | --- |
| R1 close 未收敛/失败 + Pan 断开 | watchdog 去 `_closing` 豁免；消费同一 worker；有界重试；耗尽 exit 3 | `test_r1_watchdog_consumes_late_close_success_after_lease_loss`、`test_r1_watchdog_retries_failed_close_and_exits_nonzero` |
| R2 detach↔expiry 仲裁 | 同一生命周期门 + `_expiry_in_progress` + `lease_epoch` 世代复核 | `test_r2_expiry_first_refuses_detach_zero_state`、`test_r2_detach_first_skips_late_lease_expiry`、`test_r2_late_heartbeat_invalidates_stale_expiry` |
| R3 启动失败保 owner + 可观测 | `_abort_backend` 报告/引用/重试；状态文件；退出码 4 vs 6 | `test_startup_cleanup_owner_retained_and_retry_converges`、`test_startup_refuse_real_cleans_tree_and_reports_converged`、`test_startup_refuse_uncleaned_reports_unproven_and_retains_owner` |
| O1 pipe CloseReport 丢弃 | `_close_pipe_server` 报告 + 保引用 + `retry_pipe_cleanup` | `test_pipe_cleanup_non_convergence_keeps_reference_and_retries` |
| O2 accept 失败 exit 0 | 退出码 7 + stderr 静态原因 + 状态文件 | `test_accept_failure_is_not_exit_zero_and_observable` |
| O3 join/线程回收/watchdog | 共享总 deadline（3s）、回收、watchdog 有界 join、`_finalize` 12s 预算 | `test_connection_threads_reclaimed_and_join_uses_shared_deadline` |
| O4 detail 截断 | `_bounded_json` 字段级缩减（合法 JSON） | `test_detail_generation_is_valid_bounded_json` |
| O5 预算分层 | 文档 §4.3 改写（input 0.75 / close 8 / snapshot 客户端；write budget 非硬 SLA） | 文档变更（无独立用例） |
| O6 证据更正 | 见 §4 | 本文件 + `README.md` §8 更正块 |
| O7 LOST→exit0 | `_finalize` 对 LOST 记 6（未证明），不再当成功 | 覆盖于 code（LOST 为死代码路径；无真机触发） |

## 4. 证据更正（O6）

- d48 的 `README.md` §8/§2 曾引用 `pytest.log`、`stability_run_{1,2}.log` 与开发期
  4 项失败阶段日志：这些文件**从未入库**（`.log` 被 `.gitignore:77` 拦截；工作树不存在），
  **不可核，不再声称存在**；对应结论由独立审查复跑与 JSON 证据交叉支持。
- r2 起所有日志使用 `.txt`（UTF-8）与 JSON，并入本目录；旧 `evidence/final/` 未改动。

## 5. 未验证（不得当作已解决）

- 真实 durable detach（ambient Job 下跨 Pan 存活）：仍未实证（能力注入仅机制）。
- 内核退出兜底：仅在 `startup-refuse-uncleaned` 场景实测"进程退出后树死"（该场景
  JSON 记录秒数），**不泛化为通用保证**；"已清理/未证明"以退出码 4/6 + 状态文件区分。
- Ctrl-C 产品能力、浏览器恢复、真实仿真器引擎、P2 接线：沿用未验收口径。
