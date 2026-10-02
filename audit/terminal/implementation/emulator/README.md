# P1 权威仿真器（HeadlessEmulator）实现证据

- 任务：T-TERMINAL-PTY-20261003 P1 常驻 headless 仿真器生产实现。
- 工作树：`D:/project/pan-worktrees/terminal-emulator-implement-20261003`
  （branch `implement/terminal-emulator-20261003`，起点 `8af9b0f9`）。
- 接口文档：`docs/design/PAN_TERMINAL_EMULATOR_INTERFACES_20261003.md`。
- 边界重申：全部证据为 **headless↔headless** 内部一致性；**无真实浏览器**、
  无真实模型/provider、无认证操作；不声明"无中断原生 TUI"。
- 本目录为**新增目录**：旧 CBC 探索证据（`audit/terminal/cbc/emulator/`）保持原样，
  未被覆盖；探索实现只作参考，其"保障"未照搬（MA 指出的五项残留在本目录有
  "先失败后通过"证据）。
- **r2 返工（独立审查 `1daff2a6`）**：第一轮证据（`pre_fix/`、`post_fix_*`、
  `repro_ma_gaps*`、`cleanup_scan*`）保留不覆盖；全部 r2 增量证据在
  `evidence/r2/`（UTF-8 `.txt` + `.json`），首轮被审提交为 `1daff2a6`，
  返工后提交见 git 历史。

## 文件清单

| 文件 | 说明 |
| --- | --- |
| `pre_fix/` | 基线（直接移植探索语义的第一版，保留 MA 五项缺口）的完整快照与失败证据 |
| `pre_fix/sidecar.baseline.mjs` | 基线 sidecar 源码快照（含 B1–B5 缺口） |
| `pre_fix/emulator.baseline.py` | 基线 Python 宿主快照 |
| `pre_fix/test_terminal_emulator.baseline.py` | 当时的测试文件快照（恢复入口采用 `feed_at` 直喂，暴露协议缺口） |
| `pre_fix/pytest_pre_fix.log` | 基线实跑：**10 failed / 18 passed**（28 collected） |
| `post_fix_pytest.log` | 修复后实跑：**28 passed** |
| `post_fix_verbose.log` | 修复后逐用例清单（`-o addopts= -v`） |
| `regression_all_terminal.log` | `pytest tests/ -q -k "terminal"` 全终端回归（含退出码行） |
| `pre_fix_log.json` / `post_fix_log.json` | 失败/通过清单（机器可读；pre-fix 由失败摘要行提取） |
| `repro_ma_gaps.py` | 五项 MA 缺口的独立复现/验证脚本（对基线运行五项全失败；对生产实现全通过） |
| `repro_ma_gaps_post_fix.json` | 上述脚本对生产实现的输出（`all_ok: true`） |
| `cleanup_scan.json` | 资源清理核验（无 sidecar/node/conhost 残留、无临时目录残留） |

## 复现命令

```powershell
# 基线失败（需把 pre_fix 快照放回工作位置，或按快照注释还原；不得覆盖生产文件）
# 生产实现的完整矩阵：
E:/software/miniforge/python.exe -m pytest tests/test_terminal_emulator.py -q

# 全终端回归：
E:/software/miniforge/python.exe -m pytest tests/ -q -k "terminal"

# 五项缺口独立验证（自建/自清理 sidecar）：
E:/software/miniforge/python.exe audit/terminal/implementation/emulator/repro_ma_gaps.py
```

## 先失败后通过摘要（详见接口文档 §10）

| # | 缺口 | 基线失败用例 | 修复 |
| --- | --- | --- | --- |
| B1 | 4096 事件上限掩盖未知序列 | `test_unknown_sequence_after_event_saturation_is_detected` | 逐序列即时分类（无缓冲/无上限） |
| B2 | rejected/诊断无界 | `test_feed_overflow_is_sticky_degraded_and_bounded` | 有界表 + 溢出计数 |
| B3 | feed 执行异常不降级 | `test_engine_op_error_is_sticky_degraded` | sticky `ENGINE_OP_ERROR_STICKY`；失败 feed 推进 frontier；null cursor 安全 |
| B4 | reset 误取全局 frontier | `test_reset_baseline_uses_execution_position_not_future_frontier` | 采用命令执行位置（2000 而非 3000） |
| B5 | reset 后旧 gap 污染 cursor | `test_reset_clears_old_gap_and_never_fakes_full` | reset 清旧 gap/duplicate；仍不得回到 full |

其余基线失败（属接口/语义缺口，同样先失败后通过）：

- `test_main_screen_matrix_and_recovery` / `test_alternate_screen_matrix_and_recovery` /
  `test_holdback_split_csi_and_protocol_a_recovery` / `test_holdback_split_osc_degrades_partial_and_recovers`
  （恢复端缺少协议 A"状态重建"入口 → 新增 `restore_screen()`）；
- `test_snapshot_cursor_is_applied_not_producer_frontier`
  （超时快照的 fidelity 语义与"无内容"不符 → 统一 `unavailable`）。

## 清理与资源纪律

- 测试/脚本全部使用自建 sidecar 进程：`JobObjectGuard`（KILL_ON_JOB_CLOSE）罩住，
  close 走"优雅 → 身份核验终止（同 handle）→ Job 终止"；fixture teardown 断言
  `close().closed == True`，否则测试失败。
- `test_runner_hard_death_kills_sidecar_via_job` 以子进程 `os._exit(7)` 实证
  "runner 硬死不残留 sidecar"；`test_no_process_leaks_after_close` 以
  `identity.probe_process` + raw FILETIME 复核。
- 未使用任何全局安装/服务/端口；`node_modules/` 不提交（仓库 `.gitignore` 已覆盖）。
- `cleanup_scan.json` 由扫描脚本生成（见文件内 `generated_at`），本目录证据不覆盖
  既有的其它 audit 目录。

## r2 返工证据（`evidence/r2/`；独立审查 `1daff2a6` 返工口径）

| 文件 | 说明 |
| --- | --- |
| `pre_fix_r2_tests.txt` | r2 新增测试在第一轮实现（`1daff2a6`）上的失败证据：**11 failed / 1 passed**（唯一 passed 为选中的第一轮回归项） |
| `post_fix_r2_full.txt` | 返工后全量矩阵 **39 passed**（28 原 + 11 r2；含逐用例清单） |
| `repro_p11_unbounded_close.py` / `.json` | 旧实现（`git show 1daff2a6:…` 动态加载）与修复实现的 P11/P1b **对照复现**：修正 detached holder 现场下，旧实现 `close(timeout=3)` 阻塞 12.0s、构造失败清理 15.0s 且异常无 owner；修复后 0.0s 有界收敛（`job_verified=true`）/2.05s + `owner.retry_cleanup` 收敛。脚本自证 `ok: true` |
| `core_six_uv_pyte.txt` | 六核心文件（uv 隔离 + pyte 0.8.2）：**129 passed**（`CORE_EXIT=0`） |
| `broadcast_uv_pyte.txt` | `tests/test_terminal_broadcast.py`：**8 passed**（`BROADCAST_EXIT=0`，与 129 分列；历史 137 为两者组合口径，不合并报告） |
| `summary.json` | 上述四项的机器可读摘要（含失败/通过清单） |

r2 必修项与落地（与接口文档 §12 对应）：sidecar 迁移 `packages/core/terminal/emulator_sidecar/`；
统一总 deadline 的整 Job 清理与核验（先清树 → join → 关流 → guard.close；根 DEAD 非整树空；
`BufferedReader.close` 不再无界等待）；`EmulatorStartupError` 携 owner/residual；
assign 后 `is_member`/`active` 门禁；`closed` 记账含 stderr join/三流关闭且并发 close 串行；
`diagnostics()["cursors_valid"]`；迟到 reset ack 一次回填 + 未确认期禁止旧 cursor；
`resize_wait` 确认面；清除 pre-fix 矛盾注释。

r2 复现命令：

```text
# r2 全量（39 项）：
E:/software/miniforge/python.exe -m pytest tests/test_terminal_emulator.py -o addopts= -v

# 旧/新对照复现（自建 detached holder 现场；旧实现动态加载，不修改工作树）：
E:/software/miniforge/python.exe audit/terminal/implementation/emulator/evidence/r2/repro_p11_unbounded_close.py

# 六核心 129 与 broadcast 8（分列；uv 隔离 + pyte 0.8.2）：
UV_CACHE_DIR=D:/tmp/uv-cache-pan-emulator-impl uv run --no-project \
  --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt \
  --with pytest --with pytest-timeout --with pyte==0.8.2 -- python -m pytest \
  tests/test_terminal_output.py tests/test_terminal_runtime.py tests/test_terminal_registry.py \
  tests/test_terminal_lease.py tests/test_terminal_ownership.py tests/test_terminal_driver.py \
  -o addopts= -q
UV_CACHE_DIR=D:/tmp/uv-cache-pan-emulator-impl uv run --no-project \
  --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt \
  --with pytest --with pytest-timeout --with pyte==0.8.2 -- python -m pytest \
  tests/test_terminal_broadcast.py -o addopts= -q
```
