# Clear Illegal 启动策略交付

实现提交：`25d7674e0e799bda471b07abd2f259d2dcdf62cd`。工作树 `D:/project/pan-worktrees/legal-offline-illegal-lock-review-20261018`，分支 `review/legal-offline-illegal-lock-20261018`。本轮起点 `75c6b2e74fa6d1d330ccbdb218493fe7c43711aa`。

按用户 2026-10-19 明确决定，删除设置页重复的 “Ask before reconciling facts and dismissing recovery intent”，加入英文选项 **Clear Illegal status for all Sessions and treat them as safely offline**，持久配置值为独立的 `startupPreference=clear-illegal`。

## 实际行为

- 通过现有设置 API 保存，下次 Pan 启动时自动执行，不在保存设置的当下修改 Session。
- 扫描所有当前 Illegal Session，不依赖 recoveryRequested 是否为 True。
- 取得原有 spawn/queue exclusion，核验持久 runtime owner 和事故 owner 已死亡。活/未知 owner 保留事故并报告错误，不盲目清除独立安全 gate。
- 将已确认 Illegal 的 operation 改为 `state=offline, safeTerminal=true`，写入显式用户策略退休 proof，重新聚合 legalOffline。没有其它未完成责任时成为 `legalOffline=true, illegalOffline=false`，清除恢复意图。其它正在执行的 operation 不被提前退休。
- 仅解除仍是 Illegal 来源且事故批次 ID、精确成员均匹配的消息锁；Manual（含用户已从 Illegal 转成 Manual）保留。reports 策略锁、Pause、lease、native ownership gate 不变。
- 事故 batch 保留为已恢复 tombstone，记录策略原因；执行、锁、pending/ledger、revision 通过原有有序 writer 同次原子保存。replace 失败不发布成功、不改磁盘或内存事实；重试与迟到 EOF 不重新创建事故或锁。
- 自动策略选择先持久化到既有 startup recovery record。部分失败继续其它 Session；启动弹窗展示失败原因，并可只重试已保存的 clear 策略。
- 旧配置 `sync-actual` 读取为 ask，后续保存写入规范值，不能因升级启用清事故。普通询问弹窗原有“核对事实并清恢复意图”操作依旧保留，与新策略不同。
- 策略不发继续消息或直接调用 Worker create。解除锁后仍按原队列调度，Pause 继续有效；若要暂缓消费应保留 Pause，本策略不偷偷增加队列暂停。

## 改动面

`packages/core/legal_offline.py` 新增纯账本转换；`packages/core/worker.py` 复用锁、有序原子保存、owner 核验及快照广播。`packages/web/server.py` 接入独立配置值、全 Illegal 候选和自动/失败重试路径。React 更新设置文案/类型及已保存 clear 策略失败后的重试显示。更新设计、HTTP API 参考和人工验收清单。

## 验证（本轮实测）

| 检查 | 结果 | 退出码 | 证据 |
| --- | --- | --- | --- |
| legal_offline/startup/preferences/execution_operations/main lifecycle Jobs | 113 passed，0 skipped，0 failures/errors | 0 | `audit/clear-illegal-backend.xml` |
| 真实隔离服务 + 本地 fake CLI，新策略 API 配置、重启、Manual 保留、再次重启幂等 | 1 passed，0 skipped | 0 | `audit/clear-illegal-http.xml` |
| Settings / StartupRecoveryPrompt | 59 passed，0 skipped | 0 | `audit/clear-illegal-frontend.log` |
| React tsc + Vite + 压缩产物检查 | 通过，70 assets | 0 | `audit/clear-illegal-build.log` |
| prepare_validation_env Check / commit hook / diff check | 通过 | 0 | 本轮命令输出 |

合计 173 项通过，0 failures/errors/skips。构建仅有既有 chunk 大小和 import 警告。

覆盖：新配置保存、旧 sync-actual 兼容为 ask、Illegal 但恢复意图 False 仍纳入、非事故 Session 不受影响、多个事故、手动改锁与清除串行、reports/Pause 保留、无归属匹配不解锁、独立 Running 不提前安全退休、活/未知 owner 拒绝、replace 故障回滚、失败策略重试、重复 clear/迟到 EOF 不重锁。

真实服务测试经隔离入口使用自有临时数据根、动态 loopback 非保护端口；设置通过生产 API 保存，重启后检查磁盘与 API 的 safe offline、Manual 锁、没有新 Worker（测试 Pause 保持）并再重启验证墓碑幂等。无真实远端模型调用。

## 交付边界

本轮未重新跑整个仓库、未跑远端 CI、未以实体键盘或生产 supervisor 重启服务验收。前端交互由组件测试验证，本轮未单独跑新选项的真实浏览器点击链路。共享 canonical node_modules 只读使用，没有安装依赖；dist 已重新构建，无手改产物。最终自有 isolated_http_server/fake_stream_cli 测试进程残留为 0。

未移动/合并 main、practical，未 push/release，未重启既有 Pan、未对 8767/8768 发测试请求。只保留原独立分支供验收与集成。相关 refs/listener 在协作期间可能由其它任务推进，不将其它任务变动记成本轮操作。
