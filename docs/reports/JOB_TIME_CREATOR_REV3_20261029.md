# Scheduler / Event Creator revision 2 + revision 3 接线候选

基线 `6d1b3f1c382ae80afb3ec1cbc974650243a783b0`；源码候选
`1010860c5d9873be1ad6f0db8ec2d2bc7195092f`。工作树
`D:/project/pan-worktrees/job-time-creator-line-20261029`，分支
`r6/job-time-creator-line-20261029`。本报告/证据提交只增加交付工件。
integration-only：未合并、未 push、未移动 main/practical、未操作既有服务或
8767/8768、未使用真实 data/profile/账号。

## 源→目标映射与语义

| 原路径 | 本候选复用/接线 | 验证/限制 |
|---|---|---|
| `scheduler/store.py` → `jobs/schedule_calculations.py` → `jobs/cron.py` | 保留原 host-local calculator；canonical provider 复用原 `parse_cron`、`MAX_SEARCH_DAYS`、`_day_matches`；旧与新到期决策共用抽出的 `misfire_outcome` | 同一实际 instant 对照下一 100 个 cron/0.125 秒 grid 点和 once。原 store/历史记录不重写；没有第二 calculator |
| `scheduler/time_events.py::reserve/advance` | 保留历史源；复用同一 expiry/skip/fire_now 决策 | 旧来源/旧 store 原测试保留。旧 host-local naive 与 canonical UTC 表示差异本身不是冲突；首次报告的比较结论撤回 |
| `scheduler/providers.py::TimeProvider` | 继续使用现有 Decimal/UTC、weekly/cron、grid/afterPublication 和 occurrence calculator；新增 daily，timezone/dstPolicy 可省略 | 省略 timezone 跟随当前 Pan 主机；显式 override 固定。新缺省 gap-skip/fold-first；旧显式 DST policy 仍读取/编辑，不静默改旧行为 |
| 平台主机时区 | `core/platform/timezones.py::host_zone_name`，`time.hostZone` capability，tzlocal 5.x 刷新 zoneinfo 身份 | 不以当前 offset 推算未来 DST；无配置/解析失败显式 unavailable；不修改 OS 时区/时钟。Windows 实测，Linux/macOS 未实跑 |
| 旧 nextFire/cursor/暂停/误点推进 | Creator runtime 在扫描到 reserve/paused 边界前检查实际 host zone；现有 prepare/pause/publish/advance/ack 继续承担推进 | 未预留的 calendar occurrence 在发现 zone identity 变化时，从观察时刻严格向后重算。配置 revision 不变；记录 from/to/at。错过的旧 zone 窗口不按新 zone 回放 |
| 旧 pending/已发布 Event | 不改 identity、envelope、pending next cursor、已发布 Event 或 ack | pending 先原样结算；下一轮才重算未来 host-following cursor。显式 override、once、elapsed interval 不随 host zone 改变；reserved key 不重新解释 |
| once / fractional interval | 原 offset-aware 输入与配置保留；实际 cursor/Event instant 使用 UTC，完整小数；固定 interval 为 elapsed Decimal | 所有 naive once 输入要求用户填写 offset；浏览器原生验证与后端校验共同拒绝，不添加第二确认协议。afterPublication 仍以 publication receipt 为基准，不冒称 business completion |
| calendar DST gap/fold | gap 跳过，`skippedOccurrences.reason=dst_gap` 落在持久 cursor occurrence/后续驱动证据；fold-first 只产生第一 occurrence | 不生成 gap 业务 Event；旧显式 fold-second/shift/strict 记录保留。新表单不要求 DST/fold/ordinal 字段 |
| 时间字段入口 / Template | `CanonicalSchedulerFields` 给 Event Register 与被动 `TimeTemplateFields` 共用；普通 Action/参数字段 + Add Template Scheduler 组合原 graph | 不需手工 JSON/六对象；保存 Template 不创建运行对象，不启动 Job；保留已有多规则/引用/参数/未知字段 |
| revision 3 Template 实例化 | 新 `FinalService.instantiate_time_template`、protocol、`POST /api/job-templates/{template_id}/instantiate`，React publish/instantiate 按钮 | 复用原 `CompositionCoordinator.instantiate_template`、`_operation`、requestKey scope、原查询入口。操作名已存在，无新账本。默认 activate=false，创建 disabled Job/Creator/Trigger，不新建 Run |

实例化仅支持 time/scheduler Creator 或此类共享引用。必须 published + expectedRevision，
检查 `jobTemplate.use`、实际 `job.create`、`eventCreator.create/read`、`trigger.create`；
显式 activate 才检查相应 lifecycle 与 Job execute 授权。共享来源不被隐式 enable。
Action/固定 Session target/Creator ref 校验沿现有函数；丢回执按原 operation 查询，
UI 阻止再次实例化。作用域不同的新 requestKey 才代表新实例。

FinalService definitions、`final_operations`、Creator 与 composition callbacks 实际共用
同一 CreatorStore SQLite 连接/事务；注入 links_verified 后失败的整次新调用回滚。
已有独立 composition reserved/disabled_objects_created/links_verified checkpoint 则按同一
稳定 requestKey 和 objectIds 冷重开续作。没有宣称 Event Store 与该库跨库原子。
coordinator 有明确 authorization principal 时，createdBy/owner 都使用该 principal，
避免默认 profile owner 违反 canonical public schema；未传 principal 的原独立用法不变。

## 验证入口

全部 Python 经 `python scripts/run_tests_isolated.py -- ...`，使用临时根。浏览器用自有
随机 loopback 端口与临时 profile、生产 dist、真实 Bearer HTTP；目标 Session 为 fixture
落盘记录，provider 为 RecordingProvider，Job 默认 disabled，没有真实业务发送。

- 新时间/Template：`tests/test_scheduler_time_revision2.py tests/test_scheduler_time_templates.py`。
- 扩大组合：上述两文件 + `test_platform_layer/api`、`test_job_final_creators/contracts/transports/http_service`、
  `test_scheduler_canonical_migration/time_events/store/cron/engine/pending_adoption/source_migration/creator_management/dependency_cutover`。
- 认证浏览器：`tests/test_scheduler_time_revision2_browser.py`；普通字段输入→规则/cursor 持久回读→
  Template publish→默认 disabled 图实例化，Run=0，pageErrors=0；finally 回收自有服务。
- 前端：`pnpm --dir packages/web exec vitest run src/components/automation/CanonicalForms.test.tsx src/views/AutomationWorkspace.test.tsx src/views/CanonicalJobsView.test.tsx src/services/canonicalJobs.test.ts`。
- `pnpm --dir packages/web build` 包含 `tsc -b`，另检查 88 个压缩产物；只编辑 React 源码。
- `python -m packages.jobs.final_contracts --write/--check` 生成/核对 schema；只有时间 schema/manifest 语义差异。
- 常规 `git commit` hook 完成 React frontend 检查；`git diff --check` 通过。

精确命令/计数、baseline 等价失败身份、source SHA 与文件哈希见
[`summary.json`](../../evidence/time-creator-rev2/summary.json)。已捕获的红日志保留；没有新增 skip/xfail、降低断言或吞错误。

| 最终验证层 | 本轮实际结果 |
|---|---|
| 新时间/Template 专项 | 39 passed，0 failed/error/skipped；exit 0 |
| 扩大组合 | 473 passed，15 failed，0 error/skipped；exit 1，保持红 |
| 基线红项对照 | 41 项中 13 failed + 单独 high-risk 1 failed；14 个 node identity 与候选精确一致 |
| 扩大组合额外红项 | clone-and-run transport 在 asyncio 创建事件循环的 socketpair 处 WinError 10055；尚未进入 test body。单项隔离重跑 1 passed，根因未证明 |
| 前端 focused | 4 files / 75 passed；exit 0 |
| TypeScript / production build | `pnpm build` exit 0，88 压缩产物验证；保留 chunk >500kB 提示 |
| 认证生产浏览器 | 1 passed，Chromium 153.0.8010.12，pageErrors=0；exit 0 |

扩大组合先收集后新增了一条 scan→reserve zone-change race 测试；该测试包含在最终 39 项专项中。
两矩阵有重叠，不相加冒充独立总测试数。最后源码为同一提交，无测试后产品源码修改。

## 红证据与边界

扩大回归的 15 个失败中有 14 个基线失败：13 项 scheduler pending/source migration fixture 在时间逻辑前
因 Session target 不存在被拒绝；1 项 `test_ft01_system_grant_and_high_risk_remain_separate`
同样在 enabled fixture 的 create 得到 400。分别在 `git archive 6d1b3f1c` 导出的
隔离源码复现；失败 node identity 精确对照写入 summary，未扩展本任务修改这些 fixture 或
其它 FinalService 方法。它们不证明本候选引入回归，也不允许声称整体组合已绿。
额外 WinError 10055 的完整红证据保留在 backend-final 日志/XML；单项重跑绿不能冒称
整组 488 项已绿，也不据此改事件循环或扩大进程/网络配置范围。

开发期另保留：缺 Playwright 依赖、浏览器误读 public projection 中不暴露的 cursor、
整页跳转丢失内存 Bearer、模板 owner schema 错误、平台 capability 快照尚未更新、一次
TS 类型错误及 UI 查询按钮名称错误。最终结果不混入这些中途计数。

未验证：全量后端、Linux/macOS 实机/三平台 CI、真实主机 timezone/clock 变更、真实
provider/QQ/进程执行、完整生产 lifespan、真实数据盘点/迁移/激活、部署与人工验收。
新默认与旧 gap-shift 等规则有确认过的产品语义差异；没有运行迁移或改写任何旧实例。
复杂/不可证明同 instant 的实例仍按 D6 原状保留并需显式分类，本候选不把样本对照当全体迁移证明。
