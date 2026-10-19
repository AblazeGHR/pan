# Illegal 与待恢复统一交付

实现提交：`9befa33a35861779a1559ef48c887c16b8aff58c`。工作树 `D:/project/pan-worktrees/legal-offline-illegal-lock-review-20261018`，分支 `review/legal-offline-illegal-lock-20261018`。起点 `4ea070f7fcdc24f821ebc65a61aafcd14869af05`。本报告和设计最新补充覆盖此前报告中独立恢复意图的设计。

## 最终行为

按用户明确决定：**Illegal 就是待恢复，标记待恢复就是标记 Illegal，取消待恢复就是正常安全下线。** 不再存在恢复意图与 Illegal 的两个布尔值组合。正常执行中的 `legalOffline=false, illegalOffline=false` 仍不属于 Illegal/待恢复。

- 删除 Session `recovery_requested` 模型/构造参数、持久输出、摘要签名，删除 API/WS/诊断/React 类型和状态投影里的 `recoveryRequested`，删除相应 intent helpers/writers。
- 历史 JSON 的 snake/camel 两种旧键只在加载迁移代码中接收并丢弃；启动交接准备时通过原有 writer 原子持久清理。replace 失败不放行该 Session 交接，可幂等重试。旧字段的 True/False 不推导 Illegal，因为旧实现曾在正常执行时也写 True；已有 operation/事故/来源事实保持权威。
- 启动恢复候选、自动恢复和恢复 Job 直接读取 Illegal；活 Worker 仍按既有规则排除。旧 lastLegalWorkerState 只读兼容，不用于这些决策。
- 手动标记待恢复：`POST /api/sessions/{id}/legality/mark-illegal`。取得 spawn + queue exclusion、核验运行时已停止及持久 owner 已死亡后，将 Session 标 Illegal，并对已有未锁 queued 消息建立一次精确事故锁快照。没有未完成 run 时记录 `kind=manual-recovery` 的 administrative operation，明确是用户状态修改，不冒充 provider 执行。重复标记不扩大/重锁。
- 取消待恢复：`POST /api/sessions/{id}/legality/mark-safe-offline`。同样核验停止/owner；把旧责任安全退休为 offline，原子解除仍归属事故的自动锁，保留 Manual（包括从 Illegal 转成 Manual）、报告锁、Pause、lease 和 native owner gate。对于未确认终态但 owner 已死亡的旧责任，可在同次事务内对账并安全退休；绝不把活/未知 owner 提前合法化。
- 旧 `/legality/reconcile`、`/legal-state/sync` 是安全下线动作别名；旧 `/legality/request-recovery`、`/legal-state/running` 是标记 Illegal 动作别名，均 deprecated，已不存在旧意图行为。
- `clear-illegal` 启动策略继续把已有 Illegal 改为安全下线；启动询问弹窗的取消待恢复操作现在也执行这一语义，不再只消除提示。
- 所有修改继续使用现有 Session JSON / ordered writer / queue lock，无数据库或第二套事件系统。解锁后依原队列调度，Pause 保留；按钮不发送继续消息或直接创建 Worker。

## 前端入口

详情两个按钮更新为 **Mark as Illegal / Pending recovery**、**Mark as safely offline**，反馈直接显示 Illegal / Pending recovery 或 Safely offline，不再展示 Recovery requested。API 客户端函数改为 markSessionIllegal / markSessionSafelyOffline。

同步更新设置页自动恢复/保留策略文案、启动恢复弹窗、Job 创建模板、Job 详情。既有 Job action API 标识 `resume_legal_running` 保留为持久 Job 兼容标识，但运行时只选择 Illegal，用户可见文字不再声称选择“合法 running”。

## 验证结果

全部后端命令使用 `scripts/run_tests_isolated.py` 和 canonical Python，服务测试使用自有临时数据根、动态 loopback 非保护端口及本地 fake CLI，无远端模型消耗。

| 检查 | 结果 | 退出码 | 证据 |
| --- | --- | --- | --- |
| legal_offline/startup/旧兼容状态/execution_operations/main lifecycle/preferences/native admission | 156 passed，0 skipped | 0 | `audit/unified-illegal-backend.xml` |
| Job API 与 console exit | 57 passed，0 skipped | 0 | `audit/unified-illegal-jobs.xml` |
| 最终真实隔离 HTTP + Chromium | 20 passed，0 skipped | 0 | `audit/unified-illegal-live.xml` |
| 前端 Details/启动/设置/TopBar/WS/摘要/Job 入口 | 230 passed，0 skipped | 0 | `audit/unified-illegal-frontend.log` |
| React tsc + Vite + 压缩产物 | 通过，70 assets | 0 | `audit/unified-illegal-build.log` |
| 环境 Check / commit React hook / diff check | 通过 | 0 | `audit/unified-illegal-env.log` 与命令输出 |

合计 **463 项通过，0 failures/errors/skips**。另存的 2 项定向 HTTP XML 与最后 startup 单文件复验为重跑子集，不重复计数。FastAPI testclient 有现有 deprecation warnings，Vite 有现有 import/chunk 警告，退出码均 0。

覆盖：按钮响应与跨 Session 迟到响应防护、标记 Illegal 进入待恢复候选/安全下线移除候选、对应队列锁/Manual 保留、重复标记幂等、磁盘替换失败回滚、不再输出旧字段、迁移丢弃旧键且失败拒交接、旧布尔值不制造事故、Job 触发时动态选 Illegal、多个事故/并发手动改锁、活/未知 owner 拒绝、独立辅助责任不提前退休、真正服务信号/API/q 收尾、真实 Chromium 红灯及有效继任恢复。

新详情按钮由 React 组件测试和生产 HTTP 接口服务测试分别验证；本轮 Chromium 是已有事故/恢复实时红灯路径，没有声称新增按钮的完整浏览器点击链路已实测。实际键盘/SCM/生产 supervisor、外部未观测客户端、无唯一完成身份的 compact/opaque 等此前边界仍保留。未重跑全仓库或远端 CI。

## 交付与清理

canonical node_modules 只读复用，未安装依赖，dist 已构建且未手改。交付前当前工作树自有 isolated_http_server/fake_stream_cli 进程残留为 0。未移动/合并 main/practical、未 push/release、未重启既有 Pan、未对 8767/8768 发测试请求。工作树仍保留供人工验收；当前运行的旧服务需由用户自行重启加载新代码。
