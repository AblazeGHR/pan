# Phase 4b 候选交付报告

- 分支：`feature/job-chain-phase1-20261025`
- 基线：Phase 4a `2abce39c267a4ea249c8e61a5923d1afdc19b1c0`
- 最终 SHA：以本次交付回执中的 Git SHA 为准
- interaction cards：`[]`
- 未移动 main/practical、未 push、未启动服务、未操作 8767/8768。

## 实现

- 新增 [`legacy_bridge.py`](D:/project/pan-worktrees/job-chain-phase1-20261025/packages/scheduler/legacy_bridge.py)：
  - 对旧 `scheduled-task` 记录执行显式、幂等 owner switch；保留原 `taskId`、`jobId`、schedule 和 nextFireAt。
  - 创建一个 Scheduler Event source、一个带 `payload.scheduleId` filter 的 managed Trigger，以及同一个 Job 上的 `system_scheduled_task` Action 配置。
  - 切换顺序先禁用旧直接执行，再写 Trigger/source，最后激活 Event owner；legacy `run_due_scheduled_tasks` 已跳过 Action Job，因此不会双发。
  - unknown Action、多个 schedule entry、未知 schedule 字段等无法证明无损的记录保持 legacy owner。
- Scheduler API、MCP 兼容路径和 Jobs schedule editor 在有 Service runtime 时调用 bridge；旧响应继续由 scheduler store 投影，task/job identity 和 next-fire 字段保持。
- run-now 走同一 Job 的 manual Action Run，不改变周期计划的 nextFireAt；pause/resume/update/delete 维护 source、Trigger 和 legacy projection。
- `Sources` 增加 source read、legacy next-fire adoption；旧 misfire/grace 规则仍由 source 与 Event runtime 使用。
- 未改前端；Jobs schedule editor 继续使用原 API。

## 矩阵

- 兼容矩阵：[`PHASE4_COMPAT_MATRIX_20261025.md`](D:/project/pan-worktrees/job-chain-phase1-20261025/docs/design/job-unification/PHASE4_COMPAT_MATRIX_20261025.md)
- §8 完成矩阵：[`PHASE4B_COMPLETION_MATRIX_20261025.md`](D:/project/pan-worktrees/job-chain-phase1-20261025/docs/design/job-unification/PHASE4B_COMPLETION_MATRIX_20261025.md)

## 验证

| 检查 | 结果 | 证据 |
|---|---:|---|
| Phase 4b bridge 专项 | 4 passed | `tests/test_scheduler_legacy_bridge.py` |
| Scheduler/Jobs/Action/Event/legacy touched-area backend regression | 325 passed, 1 skipped；共 326 | [`backend-regression-final.xml`](D:/project/pan-worktrees/job-chain-phase1-20261025/artifacts/job-chain-phase4b/backend-regression-final.xml) |
| Python compile | exit 0 | `python -m compileall -q packages` |
| whitespace | 待提交前执行 `git diff --check` | 收尾检查 |

专项覆盖：same taskId/jobId/source/trigger identity、单 owner、explicit switch 重试、Event payload identity、Trigger→Job system Action audit、unmappable record 留在 legacy、run-now 不移动 nextFireAt、旧 scheduler/store/API 回归、旧 Phase 4a MCP 回归。

## 未完成和边界

- §8 的真实数据批量迁移、部署切换、远程 CI、现场 MCP stdio、真实 GUI/浏览器和用户验收仍是 **partial/not done**，详见完成矩阵；本候选不宣称 §8 全部完成。
- 未测掉电级跨 SQLite/Job registry 故障、真实第三方 Provider、真实 shell/QQ provider 端到端 receipt。
- 旧 records 的自动批量迁移仍不启动；只提供显式 per-record `/api/scheduler/tasks/{task_id}/owner-switch`。
