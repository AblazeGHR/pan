# r2 返工证据（独立复核 F1–F12 闭环）

- **被审提交**：`68cf79279e78ab63f3594f4d53438f0840799b24`（P1 IPC + DPAPI 秘密存储首版）。
- **复核输入（只读，未修改）**：
  `D:/project/pan-worktrees/terminal-ipc-review-20261003`（HEAD `3d046cfa`，被审 commit
  `ccc6bd92`，证据 `f3c55521`）：`docs/design/PAN_TERMINAL_IPC_SECURITY_REVIEW_20261003.md`
  与 21 项自建负例（`audit/terminal/implementation/ipc-review/tests/**`）。
- **本次返工**：仍在原 implement 树与原写范围内
  （`packages/core/terminal/{ipc,win_pipe,secret_store}.py`、两份 tests、接口文档、自有
  audit ipc 证据目录）；未新增模块、未改 runner/core、未动 `requirements*/lock`、
  未删除/改写首轮证据（`../*.json` 保持原样）。

## 1. 先失败后通过（fail-first）

21 项复核负例**断言“缺陷存在”**，不能也不应原样保留为回归。本 TA 把它们转换为
**安全正向期望**的正式回归（`test_f1_*` … `test_f12_*`，共 34 项），并在**未修复代码**
（`68cf7927`）上先跑了一次：

| 阶段 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| pre-fix（历史产物，修复后不可重跑） | 直连解释器跑新增 34 项 | **29 failed / 5 passed** | `pre_fix/r2_new_regressions_pre_fix.log` |
| post-fix 直连 | 两份测试全部 | **114 passed** | `post_fix/direct_all.log` |
| post-fix 直连（原 80 项） | `-k "not test_f*"` | **80 passed** | `post_fix/direct_original80.log` |
| post-fix 直连（新增 34 项） | `-k "test_f*"` | **34 passed** | `post_fix/direct_new34.log` |
| post-fix uv 隔离 | 两份测试全部 | **114 passed** | `post_fix/uv_isolated_all.log` |
| post-fix uv 隔离（原 80 项） | `-k "not test_f*"` | **80 passed** | `post_fix/uv_original80.log` |
| post-fix uv 隔离（新增 34 项） | `-k "test_f*"` | **34 passed** | `post_fix/uv_new34.log` |

uv 隔离命令与复核报告一致：

```bash
uv run --no-project --python E:/software/miniforge/python.exe \
  --with-requirements minimal-requirements.txt \
  --with pytest --with pytest-timeout --with pyte==0.8.2 -- \
  python -m pytest tests/test_terminal_runner_ipc.py tests/test_terminal_secret_store.py \
  -q -o addopts= --tb=line -p no:cacheprovider
```

> 复核树 21 项原负例**断言的是缺陷存在**，因此修复后按设计**不再 pass**（例如
> `test_run_handler_executes_without_authentication` 断言未认证仍执行 handler、
> `test_close_never_converges_after_cancel_accept_rearms_pending` 断言 close 不收敛）。
> 按要求它们不原样保留为回归，而是转换为上表的正向期望；其源码在复核树中**只读保留**。

> **R2F 改名映射（复核 round2 提出，证据卫生）**：pre-fix 日志中的
> `test_f7_unknown_ace_type_and_unparsable_allow_are_rejected` 在最终树中改名为
> `test_f7_unknown_and_non_allow_ace_types_are_rejected`（同一用例：真实 DACL 的
> 未知类型 / AUDIT 类 fail-closed）。29 个失败 nodeid 中 28 个与最终名一致，仅此 1 项改名。

> pre-fix 的 5 项“当时已通过”是**正向对照**（F4 re-arm 的占名检测、F5 已 issued 的 close、
> F10 真实自证、F11 两条），与缺陷断言无关；修复后它们仍通过。

## 1b. F1 可达性分层（MA 校准，报告沿用）

- 复核树 r1 校准提交 `3d046cfa03f3a509ddd14b93cf630505785303f7`（仅 docs）明确：
  **直调 `run_handler` = 本进程公开接口契约缺口**（非远程/认证绕过）；
  `serve()` 先 `handshake()`，认证前外部不可达（失败即零 handler 调用）；
  **认证后业务帧 `terminal_id` 未与会话绑定才是真实缺口**，其跨终端影响取决于尚未实现的
  P2 handler。本 TA 的报告、闭环矩阵（`r2_closure.json` 的 `reachability` 字段与顶层
  `reachability_layering`）与接口文档 §3.1 均沿用该分层，不夸大漏洞；修复范围不变——
  `run_handler` 仍是**统一 fail-closed 门**（认证/schema/类型/绑定/期限五道拒绝，零 handler 调用）。

## 2. 闭环矩阵

`r2_closure.json` 给出 F1–F12 每项的：定级、修复点（文件/方法）、回归用例 nodeid、
pre-fix 失败情况、备注（注入类证据的边界、无法构造的 ACE 类型等），以及
`capability_changes`（**reduced 为空**：没有靠改文档缩减任何已承诺能力）。

## 3. 故障资源保留 / 清理

- 自建进程（runner 子进程、sleeper、squatter、decoy）**全部**经
  `win_pipe.terminate_verified_process`（同 handle PID + raw FILETIME 核验）终止；
  uv 蹦床下真实 runner 与 launcher 是两个各自可证的 PID，分别核验后再终止；
  **不按命令行广杀**（`test_f11_cleanup_does_not_kill_unrelated_processes_with_similar_command_line`
  用同形命令行的 decoy 进程证明其不受影响）。
- 关闭失败一律**保留资源**（`CloseReport(converged=False, closed=False)`）并可重试；
  F3 三个用例分别覆盖 connection / server / event+op 四个阶段。
- 临时目录：测试产物在 `pytest tmp_path`（pytest 清理）；返工期间的调试/探针目录
  （`pan-ace-probe-*`, `pan-acl-dbg-*`, `pan-obj-ace-dbg*`, `pan-squat-debug*`,
  `pan-impostor-debug-*`, `pan-secret-*`, `pan-ipc-smoke-*`）由本 TA 删除；同目录下其它
  `pan-*` 属其它任务，未触碰。进程残留扫描（python/uv + 测试资产脚本名）为空：
  `r2_cleanup.json`。

## 4. 未验证 / 剩余风险（不因返工改变）

1. **跨用户/远程拒绝仍未实测**：未创建账户、未改权限、未开监听。
   F7 证明的是“外部 SID 的 allow ACE 存在于 DACL 且复核层拒绝使用该秘密”，
   **不是**“第二用户实际被拒”；`PIPE_REJECT_REMOTE_CLIENTS` 只有创建参数证据。
2. **callback ACE 的真实 DACL 无法构造**（本机 `SetNamedSecurityInfoW` 返回
   `ERROR_INVALID_ACL`）：该分支用注入的 `dacl_entries` 计划结果验证白名单（用例内标注）；
   object/未知类型/AUDIT 类型使用真实 DACL。
3. 业务帧重复 `request_id` 会重复执行（无 nonce/去重，复核观察项）——
   已写入接口文档，未作为缺陷修复。
4. `serve()` 内联 handler 无执行时长上界（分发前复核期限）——文档如实标注。
5. 无长稳/压力测试；并发用例为短程确定性用例。

## 4b. 日志与 JSON 的入库策略

- **失败阶段原始日志**（修复后不可重跑）以 git add -f 入库：
  pre_fix/r2_new_regressions_pre_fix.log；
- post_fix/*.log 为**可重生成**的本地产物，按仓库 .gitignore（*.log）不入库；
  其逐用例结果与计数已解析进 r2_runs.json（含 6 个变体的 nodeid/outcome），
  collect_r2_evidence.py 可一键重跑并重新生成日志与 JSON。

## 5. 复跑

```bash
cd D:/project/pan-worktrees/terminal-ipc-implement-20261003
# 一键重跑 6 个变体并刷新 r2_*.json（约 4 分钟；pre-fix 日志为历史产物不重生成）
E:/software/miniforge/python.exe audit/terminal/implementation/ipc/collect_r2_evidence.py
```
