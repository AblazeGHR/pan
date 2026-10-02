# runner r3 窄修证据（闭环独立窄验 ROUND2 `f0bcd6fd` 的 N1–N4）

- 被审基线：`a704abbb`（ROUND2 报告 `docs/design/PAN_TERMINAL_RUNNER_REVIEW_20261003_ROUND2.md`
  @ 审查树 `terminal-runner-review-20261003`）；ROUND2 主断言（R1/R2/R3+O1、O2..O7）已实证
  通过并接受子集（暂不集成），本目录只覆盖 **N1–N4 差量**。
- 复跑入口：
  `E:/software/miniforge/python.exe audit/terminal/implementation/runner/collect_r3_evidence.py`

## 1. 先失败（a704 固定副本，只读归档，确定性门控）

`pre_fix_gates.py` 用 `git archive a704abbb packages` 导出固定副本（不改任何工作树），
以**状态级线性化点**复现（非放大概率命中）：

| 场景 | a704 实测 | r3 期望 | 缺陷 |
| --- | --- | --- | --- |
| N1-a：关闭权已取得（`_closing` 未置位）后 hb | **hb=ok、ok=True、续约生效**（假 ok） | `closing`（不假 ok、不续约） | ✅ 复现 |
| N1-b：hb 先 → 旧 expiry | `lease-skipped-renewed`、零 close（**该顺序 a704 已正确**，如实记录 defect=false） | 同 | — |
| N2：非法 terminal_id 的 status 路径 | **写出 `<root>/escape.json`（逃逸 runner-status）** | 零写 + `status-path-rejected` note | ✅ 复现 |
| N3：status 身份绑定 | 无 `runner_identity` / 无 raw FILETIME / 无 prior 辨认 | pid + raw FILETIME 字符串 + authority + prior mismatch | ✅ 复现 |
| N4：finalize 预算与锁等待 | 预算 0.6s、持锁 1.5s → **耗时 1.453s、close 已执行、exit 0** | ≤预算有界返回（锁忙→非零、不假 success、不关句柄） | ✅ 复现 |

`pre_fix_gates.json` 含逐场景实测值与 verdict；`n1b` 如实标注"两版本都应如此"。

## 2. 后通过（修复后，直连与 uv 分列）

| # | 环境 | 结果 | 日志 |
| --- | --- | --- | --- |
| 1 | 直连 `E:/software/miniforge/python.exe` | **38 passed / rc 0**（84.17 s） | `pytest_direct.txt` + `evidence_direct/`（58 份） |
| 2 | uv 0.9.14 隔离（`--no-project` + minimal-requirements + pytest/pytest-timeout） | **38 passed / rc 0**（85.52 s） | `pytest_uv.txt` + `evidence_uv/`（58 份） |

- 38 = 原 31 + r3 新增 7（N1×2 / N2×1 / N3×2 / N4×2）；未运行 core129/8、旧方向、全库、
  浏览器/provider/长稳（按要求）。
- 资源清理：21 个夹具用例、20 个句柄、**0 个在清理时仍存活、0 个需要强杀**；
  残留扫描（只读 PowerShell）**0**。
- 源文件锚定：`source_blobs.json`（暂存 blob id，与提交逐字节一致）；`uv_env.json`
  记录 uv 版本与两条实际命令行。

## 3. 闭环映射

| ROUND2 发现 | r3 实现 | 测试 |
| --- | --- | --- |
| N1 hb 与 expiry 指令级窗口 | `_acquire_lease_close_right`（lease 锁内原子复核+取得不可撤销关闭权）；`owner_heartbeat` 锁内检查 `_expiry_in_progress` → `closing`（不假 ok）；R1 同 worker 消费/重试与输入门不回归 | `test_n1_heartbeat_refused_when_expiry_holds_close_right`、`test_n1_heartbeat_first_voids_stale_expiry_and_grant_is_atomic` |
| N2 status 路径逃逸 | `_status_path` 先 `validate_terminal_id` + 目录收敛；非法 id 零写、只 `status-path-rejected`；`status_path` 属性显式抛错 | `test_n2_illegal_terminal_id_never_writes_status_files` |
| N3 身份未绑定/并发写/旧残留 | `runner_identity`（真实 pid + raw FILETIME 字符串；未核验 unknown 不造身份）+ `authority` + `prior_record_identity_mismatch`；状态写串行、唯一自有 tmp、replace 失败只清自有 | `test_n3_status_binds_runner_identity_with_raw_filetime`、`test_n3_status_tmp_failure_cleans_only_own_tmp` |
| N4 预算不含锁等待 | `_finalize` 入口起总预算含 lifecycle 有界取锁与全部 join/worker 等待；锁忙→非零保 owner 可重试；预算耗尽不静默延长（6）；finalize 结果入状态文件；非 OS 原语硬 SLA | `test_n4_finalize_budget_includes_lock_wait_and_owner_retry`、`test_n4_finalize_zero_budget_no_silent_extension` |

## 4. 口径与边界

- 旧缺失 `.log`（`evidence/final/pytest.log` 等）**不补造、不覆盖**，维持"不可核、不再声称"；
  本目录证据全部为 UTF-8 `.txt` + JSON。
- I-1..4 仍为**内部提案**（未经 MA 接口冻结批准；P2 暂不接线）；未改共享 core/backend/IPC/
  emulator/service/.workflow；无 stash/reset/push/restart/主线操作、无按名广杀。
- 维持未验收：Ctrl-C 产品能力、真实 durable detach（ambient Job）、浏览器恢复、
  跨用户/跨主机、长稳；`finalize` 预算为调用方侧有界等待（非 OS 硬 SLA）。
