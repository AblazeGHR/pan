# 旧合法状态迁移与 Pan 主服务退出交付

日期：2026-10-18。实现提交：`49b28bc5dee9b0e1abd4b1058a54126e8219656a`。

工作树：`D:/project/pan-worktrees/legal-offline-illegal-lock-review-20261018`。分支：`review/legal-offline-illegal-lock-20261018`。本轮起点：`e0f7b271ca8a2f51fd695647e6c7a7d944293803`。交付保持集成策略：没有合并/移动 main、practical，没有 push/release，没有重启现有 Pan，没有对 8767/8768 发测试请求。本轮没有远端模型消耗。

## 结果与字段契约

完成旧字段删除前的业务迁移。生命周期恢复、启动候选、后台 resume 动作、退出策略、API/WS 投影、React 详情与状态灯均使用新的执行事实/恢复意图。旧 `last_legal_worker_state` / `lastLegalWorkerState` 保留只读 JSON/API 兼容，产品生命周期停止写它；它可能保留旧值，不能再用其判断当前状态。

| 事实 | `legalOffline` | `illegalOffline` | 含义 |
| --- | --- | --- | --- |
| 全部 operation 已安全完成/退休 | True | False | 可安全离线；常驻 CLI 仍活允许 |
| 已准入执行仍在运行，或终态未证实 | False | False | 尚有责任，未判定事故 |
| 未收尾责任已经确认为异常退出 | False | True | 确定 Illegal；按失败 owner cohort 一次精确锁快照 |

`recoveryRequested` 是独立的恢复意图，不能证明 Running、Illegal 或安全终态。开始实际执行与 False/run 同次持久化设为 True；所有责任安全完成/退休或有效继任完成恢复后清除。显式 Request recovery 只设意图，不启动 Worker、不清事故；Reconcile 核对失主事实、清恢复意图，不使用 Idle 覆盖 Illegal。辅助 operation 尚未结束时，前台任务完成不能把聚合合法性设为 True。

新字段存在时优先，包括显式 False。缺字段但已有稳定 v1/v2 账本时，账本优先于旧 UI 状态。纯历史数据只有旧 running 时，导入恢复意图，不制造 run、事故或锁。迁移在启动/交接准备阶段通过现有 queue exclusion 和有序 JSON writer 原子持久化；失败拒绝该 Session 新交接，后续可重试，不加全局门禁。重复加载不覆盖显式新值。

## 本轮改动与源码证据

| 改动 | 源码位置 |
| --- | --- |
| 新持久恢复意图、优先级和迁移标记 | `packages/core/session.py:1361`, `:1570`, `:1874`, `:1919` |
| 单一恢复决策兼容入口、admission/终态意图更新 | `packages/core/legal_offline.py:24`, `:90`, `:110`, `:127`, `:253` |
| provider 前持久迁移及失主对账 | `packages/core/worker.py:654` |
| 原子修改意图及新事实返回，旧 state writer 已移除 | `packages/core/worker.py:810`, `:820` |
| 信号瞬间关闭准入并设置 host fence | `packages/core/host_server.py:6`, `packages/core/worker.py:1635` |
| 禁止迟到 ready/result/native callback 合法化 | `packages/core/worker.py:722`, `:761`, `:1296`, `:2068`, `:2103`, `:2147`, `:2194` |
| 默认 shutdown 不安全退休；仅显式 API 授权可安全退休 | `packages/core/worker.py:9971` |
| 退出快照涵盖新恢复意图或真实 outstanding，不依赖旧 running | `packages/web/server.py:630` |
| 启动候选和 Job resume 迁移 | `packages/web/server.py:5506`, `packages/core/background_jobs.py:2156` |
| 新端点和旧 deprecated 别名 | `packages/web/server.py:6906`, `:6929` |
| q 复用 Exit API、2 秒三次 q 覆盖配置 | `packages/core/console_exit.py:12`, `:67`, `main.py:394` |
| 顶部 WorkerDot 缺失 Illegal prop 修复 | `packages/web/src/components/layout/TopBar.tsx:228` |
| 详情实时诊断与新 API 操作 | `packages/web/src/components/session/SessionDetailsModal.tsx:253`, `:432` |
| 摘要 revision 保护恢复意图及诊断 | `packages/web/src/stores/sessionStore.ts:935` |

顶部不红的直接代码问题是 TopBar 没传 `illegalOffline`。服务端原已有 queue 事件摘要扩充，因此不能把之前“缺少 queue snapshot 投影”作为唯一根因。现在直接快照也带权威事实，卡片与顶部共同由新事实驱动；已做真实 Chromium 验证。

旧内部 `_record_legal_worker_state` 保留为不写数据的兼容 hook；旧 HTTP 路由保留为新语义别名。React 改用 `/legality/reconcile` 和 `/legality/request-recovery`，没有旧字段业务判断。删除旧元数据键及路由属于下一次兼容清理，不是本轮迁移必要条件，也没有进行破坏性删除。

## 主服务退出语义

- Pan 主服务 Ctrl+C/SIGINT、SIGTERM、服务管理停止、外部强杀不构成安全退出授权。信号处理立即围住新执行和迟到安全终态；能正常执行收尾时先判事故，再取消 reader/停止进程，不能借清理 kill helper 合法化。
- 强杀不能执行收尾时，已有 False/run 持久责任在下次启动对账。快照时点是持久检测边界，不能声称冻结 OS 杀进程瞬间的并发入队集合。
- 已安全 Idle 且没有其它 outstanding operation 的 Session 保持合法。Worker、Job、原生终端内部 Ctrl+C 语义保持原样。
- 显式 Exit API 与既有 Restart API 依保存/用户选择的策略收尾；安全退休要求运行时已核验停止和必要落盘成功。保留策略继续将未收尾责任作为 Illegal。既有 Illegal 仍由有效继任恢复，不因安全退出而直接清除。
- 控制台输入 q 后回车：首个 q 等待 2 秒，再按保存策略执行；ask/无配置显示 1 安全退出、2 保留 Illegal、3 强制保留 Illegal。2 秒内三次 q 或 qqq 回车提交 `preserveRunningIllegal=true`，可覆盖保存的安全策略，且首个安全请求不会先行发出。无 TTY 不读取 stdin，仍可用 API。
- 新 API 参数严格校验 bool，强制保留选择写入现有持久 lifecycle Job 快照，不仅是控制台临时状态。普通 Lock All、手动 unlock、报告锁、Pause、lease、Steer/native owner gate 和 Illegal-only 全锁不主动唤醒的约束保留。

## 验证结果（实测）

所有后端命令经 `scripts/run_tests_isolated.py`，内外均显式使用 `D:/project/Pan-main/.venv/Scripts/python.exe`；测试 Session/config/Job/terminal 注册表来自临时根。服务测试使用动态 loopback 端口并拒绝 8767/8768，CLI 是本地 deterministic fake stream provider，无远端模型调用。

| 最终检查 | 结果 | 退出码 | 证据 |
| --- | --- | --- | --- |
| legal_offline、旧状态兼容、新迁移、startup、console、execution_operations、Codex admission | 124 passed / 0 skipped | 0 | `audit/legality-migration-backend.xml` |
| main lifecycle Jobs、preferences、shortcuts、native completion/passthrough/takeover | 66 passed / 0 skipped | 0 | `audit/legality-migration-lifecycle-native.xml` |
| 真实隔离 HTTP + Chromium 最终组合 | 18 passed / 0 skipped | 0 | `audit/legality-migration-live.xml` |
| TopBar、Details、Startup prompt、Settings、WS | 221 passed / 0 skipped | 0 | `audit/legality-migration-frontend.log` |
| Session snapshot/execution/refresh/interleaving/state projection | 61 passed / 0 skipped | 0 | `audit/legality-migration-store.log` |
| React TypeScript + Vite + 压缩产物核验 | 通过，70 assets | 0 | `audit/legality-migration-build.log` |
| commit hook React 检查 | React frontend OK | 0 | 实现提交输出 |
| `git diff --check` | 通过 | 0 | 交付前检查 |

最终范围合计 208 个后端/真实服务测试 + 282 个前端测试 = 490 个通过，0 failures/errors/skips。之前分开运行的 17 项 HTTP 与 1 项 Chromium XML 同时保留，不重复计数。没有重跑整个仓库或远端 CI。构建仍有现存 chunk 大小与动态/静态 import 警告，构建退出码为 0。WS 测试有其已有 mock loadSessions stderr，测试均通过。

真实服务测试覆盖：Running SIGINT、Running SIGTERM、安全 Idle 后两种信号、API 安全与保留两种策略、已保存配置 q、ask 菜单 1/2/3、三次 q 覆盖配置；每条检查磁盘 False/Illegal/恢复意图、退出后重启幂等。q 使用生产 ConsoleExit parser 及真实 HTTP/API/Job/Worker 收尾，但未模拟实体键盘。

其它故障与回归覆盖：replace 失败不发布意图/不改磁盘；迁移落盘失败拒绝交接、重试成功且幂等；新 False/安全 ledger 胜过旧 running；Host 信号后迟到 result 不合法化；继任 ready 清恢复意图时推进诊断 revision；旧 HTTP 摘要不覆盖新合法性；详情打开时实时显示事故原因。真实浏览器测试在无刷新情况下检查卡片和 TopBar 红灯、继任就绪恢复以及 Pause 保持。

## 配置、清理和未验证边界

- 依赖准备已通过 `prepare_validation_env.ps1`。前端 junction 最终指向已有完整 `D:/project/Pan/packages/web/node_modules`，共享目标只读使用，未安装/写共享依赖；Pan-main 的依赖缺当前已有 xterm 包，不拿它的构建失败作为代码问题。React dist 是 ignored 构建产物，未手改。
- 最终没有本工作树的 isolated_http_server/fake_stream_cli 测试进程残留。观察到其它工作树已有 fake CLI，未操作。8768 listener 仍为 PID 44760，8767 无 listener；只读检查，没有测试请求或服务操作。
- Windows 真实物理键盘 Ctrl+C/q、真实 Windows SCM/第三方服务管理器的投递方式尚待人工验收。本轮是自有真实 HTTP 服务内投递 SIGINT/SIGTERM 到生产 PanServer，验证完整 lifespan；不把它声称为实体键盘或 SCM 实测。
- q 是行输入，需回车；隐藏/无交互控制台用 API。单次 q 等待 2 秒是为连续 q 覆盖窗口，已明确在提示和清单。
- 生产 detached supervisor 未由本轮服务测试实际启动；测试保留真实 API/Job/Worker 收尾，仅把 supervisor launch 替换成停止该自有 host。持久 lifecycle/supervisor 相关回归另有 66 项中的测试，但本轮不声称生产系统服务重启实测。
- 本轮 native 回归是现有 admission/operation/控制权契约测试，没有重跑真实 Codex CLI + provider 全链路；此前多 operation/本地 CLI 交付证据保留。外部未观测客户端、compact 无唯一完成身份、opaque 未知执行和 unknown owner 仍按已有保守责任/第二 writer 拒绝边界处理，没有改成按 UI Idle 猜安全。
- 未使用真实远端模型、未跑远端 CI、未做生产服务人工验收、未合 main/practical。人工清单：`docs/reports/legal-offline-multi-operation-manual-acceptance-checklist-20261018.md`，已补本轮字段、q、主服务信号与兼容迁移验收步骤。本轮实现与证据可供 MA 独立验收/集成。
