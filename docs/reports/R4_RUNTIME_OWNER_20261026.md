# R4 runtime owner 切换候选验收记录

本候选完成合成 fixture 中的 runtime owner 排他、旧 writer 封锁、WP08 离线包激活与重启恢复。
这不是生产 R4 阶段签收：没有访问真实 profile，没有取得全 profile 数量守恒、真实活资源排空或生产通知接受证据。

## 版本与边界

- 分支：`r4/owner-switch-20261025`，独立工作树 `D:/project/pan-worktrees/job-r4-owner-switch-20261025`。
- 基线：`43d0977f0c783f0bd69da5c4611d6bdfe8236a67`。
- 源码与测试提交：`e9eb213ac470df00ca04cd79a5a707552ce4a08d`；后续提交只整理本报告及证据。
- 交付为基线的线性后继，可作为快进候选。没有推进其他分支、push、启停既有服务或操作 8767/8768。
- React、MCP、CLI 未改；未删除旧 store/pass/table/handler。R5/R6 删除仍留待后续工作。

## 实现与 owner 协议

协议细节和显式激活配置见 [R4_RUNTIME_OWNER.md](../design/job-unification/R4_RUNTIME_OWNER.md)。

1. `<profile>/job-runtime-owner.sqlite3` 保存 epoch、随机 nonce 和状态；同根文件持有进程存活独占锁。
   lifespan 在任何启动任务前先锁定并以 `BEGIN IMMEDIATE` CAS 取得 `epoch+1`，再交给 FinalService；
   不按 TTL/PID 抢占活 owner。Windows 锁实现懒加载 msvcrt，POSIX 使用 flock；未知平台显式 unsupported。
2. canonical 提交必须持有匹配 epoch/nonce 的本地活锁。旧 store 提交与 claim 在同一 authority 上串行；
   HTTP/await/pass/runner 用 durable reservation 覆盖整个效果生命周期，避免跨线程等待时的锁反转。
   残留 reservation 代表未知归属，启动拒绝接管，不自动清除。closed/failed 不重新开放旧 writer。
3. WP08 包必须位于 source root 外，source root/profileId 与目标 authority 一致。
   导入前核验原文件 SHA-256、bundleDigest、planDigest、分配图、只读标记、schema v2、引用及当前授权；
   整图和 activation receipt 单事务提交，只有显式 active_ids 且离线 desiredState=enabled 的定义才 enable。
   导入器不读取旧 source schema；包的 bytes 与 runtimeActivated=false 保持原状。
4. 首次导入恢复包中 nextFireAt，不从 createdAt 重算；要求它在 canonical 时间网格上。
   缺少可确定时区、DST 歧义、计数域不匹配或活动计划缺少 next-fire 都 fail-closed。
   激活 intent 持久化；失败不能靠省略包绕过。重复启动核对 receipt，不重导、不重置 cursor 或已停用状态。
5. lifespan 取消旧 scheduler/recovery/hooks 的新接线；canonical automation 只启动 ingress/delivery → TriggerAdmission。
   background JSON writer、旧 scheduler pass/dispatcher、automation Ledger、独立 JobStore.create/retry/runner 与旧通知路径受 fence 限制。
   旧记录仍可读，旧 TriggerRun bytes 保留。执行、重试、终态与新通知统一从 canonical aggregate/outbox 驱动。
6. 重启推进 owner/Creator fence。原 Effect 按原 identity/fence 查询收据，不再 submit；明确结果恢复原 Attempt/Run，
   活执行、缺收据或 unknown 收敛 uncertain。通知先 durable sending，明确成功才 ack；发送中断/unknown 不自动重发。
   新恢复查询入口使用受控 client fixture 验证；没有查询生产 independent service。

## 验证结果

所有测试经 `scripts/run_tests_isolated.py` 清理继承 PAN 环境并隔离数据。下列计数互相重叠，不相加。

| 层/入口 | exit | passed / failed / skipped | 证据 |
|---|---:|---:|---|
| 最终组合：24 个模块 | 0 | 706 / 0 / 0 | [JUnit](../../artifacts/r4-regression-tests.xml) |
| 其中全部 `test_job_final*.py`：11 个模块，覆盖既有 R1–R3/WP01–WP08 | 同上 | 416 / 0 / 0 | 同一组合 JUnit |
| 其中 R4 专项（含参数化坏包与时间规则） | 同上 | 28 / 0 / 0 | [独立 R4 JUnit](../../artifacts/r4-owner-tests.xml) |
| 三项旧 automation 测试：基线源码对照 | 1 | 0 / 3 / 0 | [基线红证据](../../artifacts/r4-baseline-legacy-red.xml) |
| 同三项：候选源码对照 | 1 | 0 / 3 / 0 | [候选红证据](../../artifacts/r4-candidate-legacy-red.xml) |

最终组合命令（PowerShell）：

```powershell
$r4Tests = @(rg --files tests | Where-Object { $_ -match 'test_job_final.*\.py$' } | Sort-Object)
$r4Tests += @('tests/test_job_runtime_owner.py','tests/test_job_path_flip.py',
  'tests/test_platform_layer.py','tests/test_platform_import_chain.py','tests/test_automation_core.py',
  'tests/test_scheduler_cron.py','tests/test_scheduler_time_events.py','tests/test_scheduler_engine.py',
  'tests/test_scheduler_dispatch.py','tests/test_background_jobs.py','tests/test_job_system_actions.py',
  'tests/test_independent_job_bridge.py','tests/test_independent_job_service.py')
python scripts/run_tests_isolated.py -- python -m pytest @r4Tests -q -o addopts= `
  -o faulthandler_timeout=60 --tb=short --durations=15 --junitxml=artifacts/r4-regression-tests.xml
```

R4 独立入口：`python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_runtime_owner.py -q -o addopts= --tb=short --junitxml=artifacts/r4-owner-tests.xml`。
组合耗时 155.33 秒，R4 独立入口 14.11 秒。现有 timeout 配置、httpx/FastAPI、websockets、Pydantic 警告保留，没有屏蔽。

R4 专项与要求对应：

| 要求 | 测试函数（`tests/test_job_runtime_owner.py`） |
|---|---|
| CAS 竞争、双 runtime 互斥、迟到 epoch | `test_owner_epoch_cas_competition_and_stale_writers`；`test_dual_runtime_start_only_one_enters_active`；`test_old_canonical_store_handles_cannot_write_after_restart` |
| 进程 crash 与 reservation fail-closed | `test_process_crash_releases_lock_but_advances_durable_epoch`；`test_pass_in_flight_and_abandoned_reservation_fail_closed`；`test_abandoned_legacy_runner_reservation_blocks_takeover` |
| 旧写/旧 pass/旧通知拒绝，旧记录可读 | `test_old_stores_are_readable_and_all_new_writes_refused` |
| WP08 激活、坏 hash/plan/引用等回滚、失败不能绕过 | `test_offline_bundle_activation_explicit_states_and_idempotent_restart`；`test_bad_activation_rolls_back_graph_and_never_reopens_legacy`；`test_failed_activation_cannot_be_bypassed_by_omitting_bundle` |
| interval/cron 保留已审阅 next-fire，无历史补跑 | `test_activation_adopts_reviewed_next_fire_without_historical_replay` |
| 单次执行/单条 outbox 通知、重启无重复 | `test_single_execution_single_outbox_delivery_and_restart_no_replay`；`test_creator_event_single_admission_execution_notification_and_restart` |
| 活 Attempt/发送中通知 uncertain；原 Effect 查询、不重投 | `test_restart_lost_attempt_and_notification_remain_uncertain_without_replay`；`test_restart_queries_wp05b_original_effect_and_never_resubmits`；`test_notification_unknown_is_never_sent_again` |
| TriggerAdmission 唯一路径、旧 TriggerRun bytes 不变 | `test_canonical_automation_admission_keeps_old_trigger_runs_byte_identical`；`test_bound_automation_starts_admission_only_and_preserves_old_runs` |
| 跨 profile fail-closed、lifespan 最先取 owner | `test_override_store_in_another_profile_is_fail_closed`；`test_lifespan_fences_before_any_startup_and_adopts_same_epoch` |

更广组合曾暴露 background 通知测试的全局 strict callback 泄漏：单模块原有 51 项全绿，但组合中三项失败。
本候选仅补 fixture 的完整 callback 隔离，保留失败不记账、成功去重等断言；最终组合已绿。
既有重启测试也改为调用真实 `start → dispatch → close`，避免在 owner 关闭后绕过启动协议直接写 recover。

三项基线红测试分别为 `test_empty_snapshot_then_match_cannot_lose_wakeup`（等待已退役 Engine.tasks 的屏障超时）、
`test_disable_between_claim_and_dependency_deferral_cancels` 与 `test_uncertain_confirmation_keeps_run_and_new_attempt`
（后两项调用已不存在的 Ledger.claim）。基线与候选原因相同；没有改弱断言、skip/xfail 或吞掉错误。
[基线复现入口](../../artifacts/r4_baseline_probe.py) 只从 Git 读取基线模块、在隔离子进程内替换导入，不改工作树或其他 ref。
更广旧 automation 组合还曾卡在 `test_kick_read_inflight_at_close_cannot_reopen_a_run_or_leave_timer`，因此不声称完整 backend 绿。

## 证据与未测层

[脱敏 manifest](../../artifacts/r4-owner-manifest.json) 固定 sourceSha、路径范围、精确选择、计数、返回码、测试节点及 JUnit SHA-256。
JUnit 移除主机名和用户安装路径；未提交原始用户数据或账号信息。源码 diff 检查通过，普通 commit hook 未绕过。

- 实测为 Windows 本地 fixture，包括现有临时 TCP HTTP/stdio、子进程与进程锁崩溃恢复。没有启动完整生产 lifespan；新增 lifespan 顺序断言使用受控 inner context。
- 本轮未执行真实 Ubuntu/macOS，平台测试含模拟导入/AST 门禁，不能替代三平台 CI；远端 CI、依赖安装门禁与发布均未操作。
- 未改前端，未运行 React build/浏览器 UI 验收；未访问真实 provider/QQ/Session 账号或真实生产通知接受端。
- 同 authority 的本地 sibling stores 才属于本次 fence cohort。另一 profile 的旧 store override 明确拒绝；不同根的独立宿主、旧二进制、网络文件系统与跨主机 owner 未证明。
- 活外部效果没有自动取消或重投；启动时未知结果保持 uncertain。后续外部收据轮询、人工处置或排空须另行验证。
- 没有真实副本迁移、crash/rollback 演练、全 profile 守恒、复杂实例保留清单、部署 readback 或人工接受。`activeLegacy`、`legacyExecutableRecord` 与 `unknownOwnership` 的生产值保持未测，不能写成零。

因此本交付的结论为 **R4 runtime fixture 候选通过；生产接管与 R4 全阶段验收未签收**。
