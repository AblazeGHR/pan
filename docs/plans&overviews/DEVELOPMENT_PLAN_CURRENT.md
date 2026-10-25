# 当前开发计划（滚动快照）

- 更新：2026-10-25
- 真源：`D:/project/Pan/data/workdirs/PAN_SMA/.workflow/overview.md`（MA 的实时进度与精确对象 ID）
- 本文件只保留“不能丢”的计划与依赖关系；完成项移入“最近完成”，避免历史堆积。

## 进行中

| 工作 | 状态 | 对象 | 下一步 |
|---|---|---|---|
| Job 统一 / Action 链 / Scheduler Event（Phase 1–5） | 已实现并验证，**仅在集成树** | 分支 `feature/job-chain-phase1-20261025`，集成树 `integration/job-chain-20261025` @ `b6f0e388` | 等用户命令后推进 main；真实数据迁移/部署/远端 CI 未做 |
| Terminal 无法启动（starting → clean up failed） | 修复中 | Session `ses_c461b12d43ab4394`，分支 `fix/terminal-cleanup-20261025` | 定位 launcher/runner 清理失败根因；隔离实例复现；回归后快进 |
| 跨平台 Phase A（解锁 + CI） | 并行实施中 | `packages/core/platform/`、导入链、CI | 三平台 import 绿；能力矩阵初版 |
| 跨平台 Phase B/C（进程所有权 / 前端能力） | 并行实施中 | 见 `CROSS_PLATFORM_PLAN_20261025.md` | 契约测试；不阻塞功能开发 |

## 最近完成

- cc-switch 插件非手动门禁（main `3d84c769`，practical `ceaba292`）
- 面板排序 + Chat/Locker 未读徽标 + 匿名未读汇总（main `399b86a9` 起）
- Session 卡片移除工作目录展示（main `58c0739a`，practical `51916ea0`）
- dsh / kimi adapter 与 native TUI（main `3bf174b1`）
- practical 已同步至 `51916ea0` 并构建（90 压缩资产）

## 约束（不变）

- 单 main；平台差异只进平台层；共享代码零 win32/路径硬编码（`AGENTS.md`）。
- 平台相关功能必须有 capability，隐藏/禁用要显式。
- 每个任务独立验收；组合验证通过才推进目标分支；push/CI/服务重启/真实数据迁移需单独授权。
- 8767/8768 与真实 profile/data 不用于测试。
