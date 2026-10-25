# Phase 4a 候选交付报告

- 分支：`feature/job-chain-phase1-20261025`
- 基线：Phase 3 `99b7623c87aaafc73ec5563b908f7a5135420f1c`
- 最终 SHA：以本次交付回执中的 Git SHA 为准
- 交互卡：`[]`。没有遇到需要用户冻结且会改变既有用户行为的阻塞选择。
- 工作树、main/practical、远端、服务和 8767/8768 均未被操作。

## 实现

- 新增 [`legacy_compat.py`](D:/project/pan-worktrees/job-chain-phase1-20261025/packages/jobs/legacy_compat.py)：把可无损映射的 legacy MCP background process、single message、broadcast 参数转换成统一 Action Job；保留 legacy `kind`、目标、schedule、错误/通知所需字段。
- 扩展 [`action_runtime.py`](D:/project/pan-worktrees/job-chain-phase1-20261025/packages/jobs/action_runtime.py)：兼容记录仍由同一 Action Job registry、Run/Attempt audit、授权、取消和恢复生命周期管理；不再由 MCP 创建旧顶层执行记录。
- 扩展 [`server.py`](D:/project/pan-worktrees/job-chain-phase1-20261025/packages/web/server.py)：trusted MCP IPC 在原 legacy 路径内分流；普通 HTTP/GUI 路径继续保持旧 owner。查询、列表、取消、更新、重试投影旧返回形状并使用同一 `jobId`。
- 扩展 [`authorization.py`](D:/project/pan-worktrees/job-chain-phase1-20261025/packages/automation/authorization.py)：仅增加 legacy MCP 路径的私有 IPC allowlist；不会把普通 HTTP 变为新对象代理。
- 未改前端；旧 Jobs 入口和 GUI 仍可见。

完整矩阵见 [`PHASE4_COMPAT_MATRIX_20261025.md`](D:/project/pan-worktrees/job-chain-phase1-20261025/docs/design/job-unification/PHASE4_COMPAT_MATRIX_20261025.md)。

## 验证

| 检查 | 结果 | 证据 |
|---|---:|---|
| Phase 4a compatibility 专项 | 4 passed | `tests/test_legacy_mcp_compat.py` |
| Phase 4a + Phase 1/2 automation/jobs/scheduler/legacy/authorization 回归 | 565 passed, 1 skipped | [`backend-regression-final.xml`](D:/project/pan-worktrees/job-chain-phase1-20261025/artifacts/job-chain-phase4a/backend-regression-final.xml)；pytest 总计 566 |
| 既有 legacy HTTP/API 回归 | included above | `test_background_job_api.py`, `test_legacy_job_transition_http.py`, `test_session_message_jobs.py` |
| Python compile | exit 0 | `python -m compileall -q packages` |
| whitespace | 待提交前执行 `git diff --check` | 收尾检查 |

专项覆盖：MCP 旧创建形状、system Action ID、同一 Job ID、Action audit、single/broadcast target 映射、trusted MCP retry key 幂等、取消/查询路径、unknown 结果重启后不自动重放、未迁移旧 HTTP/GUI 路径继续工作。

## 全量测试边界

另外运行了 `python -m pytest tests -q` 作为环境审计，JUnit 保存在压缩证据 [`backend-full.xml.gz`](D:/project/pan-worktrees/job-chain-phase1-20261025/artifacts/job-chain-phase4a/backend-full.xml.gz)。该命令不能作为本候选的绿色回归证据：首个失败来自既有真实 HTTP 测试固定使用受保护端口 `8767`，随后包含需要真实服务/浏览器/原生终端进程的套件失败。按照任务边界，本阶段没有启动这些服务、接管这些端口或修复无关环境问题；Phase 4a 的相关 backend 回归使用上表中与 Phase 2 相同的隔离文件集合并已通过。

## 未迁移 / Phase 4b

- 普通 legacy HTTP/GUI creation 仍由旧 owner 处理；本阶段只切受信任 legacy MCP creation entries。
- `scheduled-task`/shell、QQ、`resume_legal_running`、`main-lifecycle` 等不能证明无损的形状保留旧路径或受保护 supervisor 路径。
- Schedule record → Event source → Trigger → Job 的单 owner 切换、旧时间记录迁移、Scheduler 入口切换和真实数据迁移全部留在 Phase 4b。
- 未测真实生产 MCP transport、真实第三方事件源、掉电级文件系统故障、远程 CI；本阶段未改前端，因此不需要 tsc/build/资产重跑。
