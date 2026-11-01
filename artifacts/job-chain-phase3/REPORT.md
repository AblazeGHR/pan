# Phase 3 候选交付报告

- 分支：`feature/job-chain-phase1-20261025`
- 基线：Phase 2 `b83745c5199b88b9ca3a688085e2fd7c7079013d`
- 本候选提交：见最终 Git SHA（仅隔离工作树；未 push、未移动 main/practical、未启动服务）
- 交互卡：无（`[]`）。本阶段没有遇到会改变既有用户行为且阻塞实现的未决选择；非阻塞 §9 选择已写入协议文档。

## 交付内容

- Automation 页面增加 Job、Trigger、Event Register 三个 tabs；Job 链编辑器支持稳定 stepId、顺序操作、参数映射和校验；Trigger 显示 owner；Event Register 展示 schema、revision、next fire、时区和订阅影响。
- 两条 Scheduler flow 使用 durable operation key：选择已有来源或创建新来源并建立 managed Trigger。创建、重试、失败隔离、启动恢复、revision/impact 冲突和生命周期规则均由后端 `Links` coordinator、`job_link_api` 和 Scheduler source receipts 实现。
- 旧 Jobs 入口和旧 API/MCP 路径保持不变；现有带时间触发的非 manual Job 不会被 UI 静默迁移。
- Phase 2 Event/Scheduler identity、cursor、广播隔离和 Phase 1 chain target 继续作为下游路径；managed Trigger admission 与 Job chain completion 分开审计。

## 验证证据

| 检查 | 结果 | 证据 |
|---|---:|---|
| Phase 3 后端专项 | 14 passed | `artifacts/job-chain-phase3/links-post-guard.xml` |
| Phase 3 + Phase 2 + 既有 automation/jobs 回归 | 531 passed | `backend-final.xml` |
| 前端目标与既有回归 | 98 passed | `vitest-final.xml` |
| TypeScript | exit 0 | `tsc-final.log` |
| production build | exit 0 | `build-final.log`，`Verified 92 compressed production assets` |
| 生产 bundle UI/键盘检查 | 21 screenshots；1440/390/320；0 overflow；0 page errors；legacy Jobs entry=true | `browser/manifest.json` 和同目录 PNG |
| Python compile | exit 0 | 收尾 `compileall` 命令 |
| whitespace | 待提交前执行 `git diff --check` | 收尾检查 |

后端专项覆盖：已有/新 Scheduler flow 不合并、同 key 幂等、失败 quarantine 和 retry、activation/write failure fail-closed、共享来源生命周期、revision/impact 冲突、多个 link 的稳定 Trigger、source disable 不影响已 admitted chain、用户授权、输入映射冻结、重启后 Store 重建恢复。前端测试覆盖 tabs/键盘、链增删重排和引用校验、literal/input/event/prior-step 映射、两条 flow 的 retry、Event Register 引用/生命周期、revision rebase、草稿保护和旧 Jobs 入口。

## Event identity/cursor contract

occurrence identity 使用 `scheduleId + scheduleRevision + scheduledAt`；持久化 Event 另有 `eventId`、`emittedAt`。Scheduler 的顺序是：计算 due → durable Event admission（按 occurrence key 唯一）→ durable publish cursor advance。crash 只允许发生在 admission commit 与 cursor commit 之间；重启重复计算时按 occurrence key 去重，所以不会 double-emit。Event admission 未成功时 cursor 不前进。missed policy、timezone 和 recovery 仍由现有 Scheduler 负责。

## 未决/未测边界

- §9 的 event type/field paths、publish cursor 存储形态、missed policy 映射和多 Trigger 语义采用现有 Phase 2 `scheduler.time.v1`、`payload.scheduleId`、occurrence-key 去重、各订阅独立 receipt 的默认；替代方案及影响记录于 [AUTOMATION_UI_LINK_PROTOCOL_20261025.md](D:/project/pan-worktrees/job-chain-phase1-20261025/docs/design/job-unification/AUTOMATION_UI_LINK_PROTOCOL_20261025.md)。
- 未测真实移动 OS/触摸/现场浏览器、真实第三方 Event source、真实生产 API、掉电级文件系统故障和远程 CI。Playwright 证据使用 isolated virtual origin fixture，不使用 8767/8768 或实际服务。
- 未实现 Phase 4 的旧 MCP wrapper、真实数据迁移和旧入口切换；未实现 Automation UI 之外的后续 Event Register/Trigger migration。
- 初次依赖目录缺失导致的 Vitest 红证据保留于 `red/vitest-dependency-red.xml`；早期截图断言红证据保留于 `red/` 和同目录 browser red manifests，最终 manifest 为绿色收尾结果。
