# 组合 r2 证据（修正后真实验收；seed 26b7086b）

- 工作树：`D:/project/pan-worktrees/terminal-composition-r2-20261003`
  （branch `audit/terminal-composition-r2-20261003`，seed `26b7086b`，含 emulator r4 F1/F2
  与 Runner F5 最终 `29d535bc` 的隔离集成）。
- 依据：`PAN_TERMINAL_COMPOSITION_BOUNDARIES_20261003.md`（含 2026-10-03 后续验收状态）、
  `PAN_TERMINAL_RUNNER_F5_ACCEPTANCE_20261003.md`、emulator r4 接受、首轮组合报告
  （`7e6b30cb`，本树以 `cherry-pick -x` 导入为历史，原证据不刷新）与独立审查 `cabacc7f`。
- 写范围：`tests/test_terminal_composition.py`、本目录、`docs/design/PAN_TERMINAL_COMPOSITION_REVIEW_20261003_ROUND2.md`；
  **任何 packages/生产/共享契约/服务/Web/MCP/registry/总依赖零改动**；首轮 evidence 历史不刷新。

## 相对首轮的修正与新增（真实验收）

| # | 内容 |
| --- | --- |
| F1 | `ready 前 EOF` 在 emulator r4 后**必须** `EmulatorStartupError` 带可重试 owner：测试由"记录现状"改为**硬断言**（owner + retry_cleanup 收敛 + 无 node 残留）；spawn 前失败仍 owner=None |
| F2 | 容量证据改为**单一 monotonic**（首次提交 → `applied==total`，不扣除任何 probe 窗口）；`producer_feed_ops` 与 `engine_feed_batches/feed_batched_ops/max_feed_batch_bytes` **分列**；同机探针、不外推跨机硬 SLA |
| F5 | 新增真实 `engine↔runner↔IPC` 机器字段验证：字段存在且 **bool-or-null**；reset 未确认窗口内短超时快照 → `reset_unconfirmed=true`（`cursors_valid=false` 为引擎在未确认期的自身语义）；迟到 ack 回填后 `reset_unconfirmed=false` 且 `cursors_valid=true`（stub 回填 baseline==applied）；**node 原因只在 note、Python reasons 为空不得升级**（partial 不放宽白名单） |
| 协议 A 边界 | 新增驱逐边界门控：applied < first_retained 窗口内 `read(applied)` **显式 gap** + 窗口内数据；fresh-view 语义=不自动 `reset_baseline`、不杀 PTY；追平后重取快照无 gap |
| 宿主收尾 | 测试宿主（**非生产 launcher**）在 `runner.run()` 返回后有界 `close()`：正常结束（closed/job_verified 实测）；新增 `close(timeout=0)` → `closed=false` 有界报告 → 有界重试收敛（owner 可重试语义）；启动失败 owner 重试见 F1；硬死布局（Job guard 内核兜底）单独留证且不泛化 |
| 保留 | 独立心跳与尺寸分列、disconnect 不杀、write worker 不阻塞、detach 受限拒绝（不逃脱）、真实会话 partial 为常态（不阻白名单） |

## 证据

- `pytest_direct.txt` / `pytest_uv.txt`：13 项（11 修正基础 + 2 新增）直连与 uv 各一次；
- `evidence/direct|uv/`：每用例 JSON（容量、F5 字段、gap/fresh-view、宿主收尾等）；
- `sidecar_pins.json`：exact pin `@xterm/headless@6.0.0` + `@xterm/addon-serialize@0.14.0`
  （本树 `npm ci`，未改 lock）；`source_blobs.json`：暂存 blob 锚定；
- `summary.json`：环境、退出码、耗时、清理汇总、残留扫描（python runner / node sidecar）。

## 复跑

```bash
E:/software/miniforge/python.exe audit/terminal/implementation/composition/r2/collect_r2_evidence.py
```

清理只用自有 retained handle + raw FILETIME + Wait；无广杀/stash/reset/全局记忆/push/build/restart；
不接 P2、未实现生产 launcher。
