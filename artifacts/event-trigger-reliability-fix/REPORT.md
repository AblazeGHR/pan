# Event/Trigger 六项可靠性风险：复现与最小修复候选

输入：`57fd6df6940c0b6a2d9a92491450b26bf241a106`。
独立后续树：`D:/project/pan-worktrees/event-trigger-reliability-fix-20261023`。
分支：`fix/event-trigger-reliability-20261023`。
**精确最终受测源码 SHA：`0de4d769659ccd38afb27a0cd67b696d1c11b94a`**。
后继提交只保存本目录报告、命令和运行证据，不改产品/测试源码。

原静态审计见 [07150f59 报告](D:/project/pan-worktrees/event-trigger-reliability-20261023/artifacts/event-trigger-reliability/REPORT.md)。本轮针对最新整合代码重新复现，没有把原静态推断当作运行证据。

**结论：六项均复现并已修；无排除项、无实现阻断项。** 本地候选待 MA 独立验收，不宣称 main 已合入。按本轮策略，最终仅允许 MA 推进 main，practical 暂不合并/移动。

## 六项逐一关闭

| 项 | 修前确定性证据 / 触发 | 最小修复位置 | 修后断言 / 状态 |
|---|---|---|---|
| F1：claim 后准备期间撤权 | 在 Action read、target checkpoint 返回处分别持有屏障，撤销 Bus 运行时后释放；修前仍调用 Pause，`calls == []` 失败 | [engine.py:297](../../packages/automation/engine.py#L297)、[executor.py:70](../../packages/actions/executor.py#L70)、[provider.py:38](../../packages/actions/provider.py#L38) | 准备后与实际业务调用前复检当前依赖；尚无 effect 则用最新 revision defer 同一 Attempt，重新注册后同 Run/Attempt 只执行一次。已做首目标后再撤权则 uncertain，保留首目标成功，不自动重投。**已修** |
| F2：inline publication 阻塞 reader ACK | hold publish 的 admission，向同一 reader 输入 publish，再输入既有 delivery ACK；修前 ACK future 超时，release 始终未放行 | [event_adapter.py:69](../../packages/plugins/event_adapter.py#L69)、read、[publish:232](../../packages/plugins/event_adapter.py#L232)、_close | 一个固定 publication owner + 128 项有界队列；reader 可继续解析 ACK；队列满明确返回错误，无每帧 task。新增 queue-full 和取消持有真实磁盘提交的屏障，提交结算前 owner 不退休。**已修** |
| F3：一次 I/O 错误使 queued 滞留 | 已持久 queued，owner 第一次 read 或 claim 单次抛 OSError；不发送新 Event/外部 kick、不重启，修前 effect_done 超时 | [engine.py:164](../../packages/automation/engine.py#L164) | 保留同 Trigger owner，仅在 I/O/容量故障后用 signal 可中断退避（0.25 秒起，上限30秒）重读/claim；正常队列无轮询。修后原 Run 一次 Attempt 完成；close 继续取消/加入 owner。**已修** |
| F4：maintenance 旧 retainedIds 删有效去重 | 删除旧 Event 后暂停维护；同 ID 重新接纳并 matching 到 delivered；再释放 prune，修前旧 Run 被删，当前 Event 丢失其 matched pair | [service.py:89](../../packages/automation/service.py#L89) | 复用已有 admission→matching 顺序协调两份存储的维护窗口；不持锁等待 Action/provider。修后 readmission 受控等待，合法过期剪枝后生成一次新生命周期，再 replay 不建第二 Run。**已修** |
| F5：过期 Event + 保留 Run 同 ID 不同事实 | queued/retry_wait/uncertain、未知 finish timestamp 的终态分别保留；Event 到期删除后以不同 payload 重发，修前未拒绝 | [service.py:59](../../packages/automation/service.py#L59)、[event_store.py:21](../../packages/automation/event_store.py#L21) | 接纳时按仍保留 Run 的 source/type/version/payload 身份核对；冲突显式拒绝；相同事实保留原 envelope、duplicate:true，不重置 Run/Attempt。无需永久 tombstone 或新 schema。**已修** |
| F6：cancel serial 退避队首不唤醒尾部 | 将 clock 固定1000，队首 due1060、尾部queued；owner 已读到队首后通过 ASGI cancel，修前尾部 watchdog 超时 | [api.py:165](../../packages/automation/api.py#L165) | 持久 cancel 成功后 kick；不推进 clock，尾部即可执行，原队首 attemptCount 仍0。**已修** |

F1 的“原始发布者 generation”不会钉死到旧连接；历史 Event 仍能在可信新 registration 后继续。第一次 effect 已开始后的撤权保守标 uncertain；不把已经产生的 effect 变成可自动重复工作。Locker 原有 `verify_interaction` 每 effect 调用保留，事件依赖 gate 位于该验证之后；多目标回归证明首目标后 Action 改变可挡住第二目标。

没有更换 engine/Job 生命周期，没有改动态 Job 引用、Job snapshot/通知、report 格式、Queue ID、unknown 回执规则、plugin Session 绑定或 generation authority。共享 provider 的新增 guard 是可选 keyword；旧 Group 调用维持原签名兼容和既有校验。

## 修前与过程证据

命令前缀统一为：

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest <selection> -o addopts= -q --junitxml=<output>
```

解释器只读复用 canonical `.venv`，没有安装依赖。所有运行的 cwd 都是本后续树。`scripts/run_tests_isolated.py` 移除继承 PAN 配置并分配随机测试端口；共享 pytest fixture 将 Session/Action/Job/Config 根隔离到 tmp_path。

| 检查 | selection / 证据 | 结果 |
|---|---|---|
| 未修产品初轮 | 当时新文件的11项屏障；[before.log](before.log)、[before.xml](before.xml) | **10 failed、1 passed，exit1，12.00s**。其中 maintenance 用例初版没有强制目标交错，不能当作该风险排除证据 |
| 未修产品 maintenance 精确交错 | `tests/test_event_trigger_reliability.py::test_maintenance_and_readmission_preserve_current_event_dedup`；[before-maintenance.log](before-maintenance.log)、[XML](before-maintenance.xml) | **1 failed，exit1，0.86s**；修正测试屏障后出现 `retained Event lost its matched dedup record`。这次仍是修改产品前，保存两次原始证据 |
| 最小修复初轮 | 新文件当时11项；[after-first.log](after-first.log)、[XML](after-first.xml) | **11 passed，exit0，2.00s** |
| 相关回归首轮 | core/concurrency/retry/event-store/plugin/lifecycle/Locker/new barriers 八文件；[regression-first.log](regression-first.log)、[XML](regression-first.xml) | **138 passed、2 failed，exit1，24.26s**；两项为旧测试脱节，见下文 |
| 跨线/副本/旧 fixture 修正后 | [cross-line-first.log](cross-line-first.log)、[XML](cross-line-first.xml) | **89 passed，exit0，12.51s** |
| 隔离 HTTP 初轮及新增屏障 | new barriers + event_store_http + terminal_report_http；[http-first.log](http-first.log)、[XML](http-first.xml) | **17 passed，exit0，19.68s** |
| 最终新增屏障 | 当前新测试文件；[barriers.log](barriers.log)、[XML](barriers.xml) | **15 passed，exit0，2.58s**；包含多目标 Locker gate |

这些批次重叠，**不相加**。失败没有转 skip/xfail、没有减弱业务断言。修前/修后异常正文均保留。

原始失败输出含 pytest 生成的行尾空格，首次 artifacts diff-check 因此报错。为满足仓库文本差异检查，展示用 log/XML 仅规范 UTF-8 BOM、换行与行尾空白；任何发生规范化的文件，其逐字节原始数据均另存 `.raw.gz`，SHA256/路径见 [raw-evidence.json](raw-evidence.json)，没有删除失败信息或替换断言。规范化后再做 staged diff-check。

首轮回归两处 fixture 修正位于 `tests/test_plugin_events.py`：

- slow subscriber 原用例仍调用 durable Bus 禁止的同步 publish。现先暂停自有 dispatcher、await 持久发布 burst，再对已落盘 envelope 做同步 live fanout，保持原 `rejected`/queue128、独立 peer 前进和慢消费者退休断言。这样队列容量测试不再把 SQLite await 的耗时误当成 fanout 排队时间。
- restart fixture 给 fake worker 补齐与普通 setup 一致的临时 `_sess.SESSION_DIR`，没有删除或绕过 quota/Job 启动路径。

## 最终冻结源码验证

**224 passed、0 failed、0 errors、0 skipped，exit0，49.02 秒。**

- 精确 argv、cwd、源码 SHA、exit： [validation.json](validation.json)。
- JUnit：[final.xml](final.xml)；完整输出：[final.log](final.log)。
- 可直接复跑：`D:/project/Pan-main/.venv/Scripts/python.exe artifacts/event-trigger-reliability-fix/validate.py`。脚本只运行记录的定向选择，保存本轮生成的证据后恢复被测试覆盖的历史 artifacts；不重置产品/测试文件。命令和结果不会继承真实服务配置。
- 一条既有 `pydantic_settings` lifespan unresolved forward-reference warning；未 suppress，非新增 skip。
- `python -m compileall -q packages/automation packages/actions/executor.py packages/actions/provider.py packages/plugins/event_adapter.py`：exit0。
- Git source diff / staged diff whitespace 检查单独执行；没有前端修改、build 或重复浏览器验证。

选择包含六项风险屏障、core/并发/有限重试、Event Store、插件真实 Node 生命周期、Locker trusted/independent、动态 Job 模板引用/ephemeral snapshot、report 与 RuntimeIssue 共存、原生诊断、Action-bound Queue unknown 不重投、旧 Job/Inbox 副本迁移、三个 query 冻结与恢复用例，以及以下三条真实隔离 HTTP 测试。不是全仓全量。

## 真实隔离 HTTP / fakeCLI 与清理

最终批次顺序复用随机空闲端口 **63665**；每次实例关闭并确认端口释放后才启动下一实例，不接触8767/8768。

1. [core-running/http-evidence.json](observed-final/artifacts/event-store/core-running/http-evidence.json)：自有 HTTP + fakeCLI 的真实 Worker 正数→0→SQLite Event→query→原 Pause Everything；目标待处理队列不交付，未选控制 Session 的 fakeCLI 实际消费。服务 PID **6480**，出生时间与命令在证据中；poisoned-parent 为自有 sentinel，内容保持不变。
2. [Node HTTP evidence](observed-final/artifacts/event-store/http-evidence.json)：真实自有 Node stdio 发布→persisted receipt→query/Pause；Bearer replay 不增加 Run；重启同 ID 重发得到 duplicate；自有离线 running fixture 重启后 uncertain，replay409；无真实provider。PID/出生时间/命令记录在 `processes`。
3. [terminal-report evidence](observed-final/audit/terminal-report/http/evidence.json)：真实 HTTP/MCP subscribe+assign、fakeCLI 原生 error+assistant result 共存；冷读 report 不依赖 error JSONL，原 queueItemId `q_26d3d345154e4b9ebb732ec22b61d2ab` 保留，最后由管理 Session 的 fakeCLI 消费诊断正文。[delivered.txt](observed-final/audit/terminal-report/http/delivered.txt)。

三份证据均为 `ownedProcessesRemaining:[]`、`portReleased:true`。没有操作既有进程/服务。新增插件 owner 在 unit teardown 与真实 Node lifecycle cleanup 中均加入；被取消的持久写在线程结算后才允许 publication owner 结束。真实硬件断电不是这些屏障的等价证明。

历史测试固定路径产生的新 transport/HTTP 输出已复制到本目录 `observed`（过程）和 `observed-final`（冻结运行）中，原 tracked artifacts 恢复输入字节；最终候选不覆盖它们。`observed-final` 仅保留本轮生成的九项输出，不混入历史 JUnit/截图。

## 旧版数据兼容、临时副本和主线门禁

**本补丁没有改存储格式或增加迁移器**：SQLite schema/user_version 仍1，Event fingerprint 的完整字段序列化保持兼容；Run/Attempt JSON 字段未增加或改名。新行为只使用现有 Run 内嵌原 Event 做身份核对，并协调 admission/matching/maintenance。两存储之间依然是分开提交，不能假装跨库事务。

新 `test_old_profile_copy_reopen_failure_recovery_and_repeat_maintenance` 构造旧版 schema-v1 inbox 和无 attempts 的 v0.1 running Run（含原 targets、成功 results、未知扩展字段），**先 copy 到另一临时根**，只在副本上运行：

- 注入 SQLite maintenance 已结束、JSON prune 写入失败；错误明确抛出，重新维护可继续。
- 旧 running→一个 inferred legacy uncertain Attempt；重复 initialize/recover/maintenance 两次，Attempt 身份/数量不增加，冻结 targets/results 与 extension 不丢。
- 过期后同 ID 不同事实仍拒绝，相同事实原 envelope 去重；源目录逐文件字节与复制前完全一致。

同批既有 `test_job_inbox_migration_drill`、Locker 旧 unbound/unknown-recipient copy tests 覆盖本整合的其他存储投影与不猜绑定；query/retry/Queue unknown 回归保留既有恢复边界。测试通过是**合成旧格式临时副本**证据，不是 practical 真实数据已升级/验收的事实；本轮未读取或写其真实 data。

可验证迁移/部署路径供 MA 作为 main 门禁：停写并备份完整 profile（包含 Event SQLite、Run/Action/Job/Inbox，不只单个数据库）；在自有临时副本用同版本单 writer 打开，执行本报告对应恢复/去重检查；确认原备份字节不变、unknown/uncertain未自动 effect、维护出错可重入，然后再决定部署。现有服务本轮不停止、不替换。

**降级/混合 writer 限制**：禁止不同版本并发写同 profile；旧 writer 不知道本补丁的跨存储协调与保留身份检查，即使 schema 能读取也可能重新引入风险。更旧版本还可能不理解 retry_wait/parallel/Job/Locker 数据。回退应停止新 writer 后恢复一致的整份升级前备份，在单 writer 下启动；不能在新格式/新运行事实上直接降级，也不声称可回滚已产生的业务 effect。容量仍显式拒绝，pending/uncertain 仍受保护；没有新增自动清理/自动重试 unknown 的路径。

## 边界与交接

本轮没有改原 `eta-job-report-integration-20261023` 树；所有产品修改与运行产物在本独立后续树或自有 tmp profile。未动 main/practical、真实 data/provider/QQ/账号、OAuth/keyring/exporter、保护端口及既有服务；未安装依赖、push/release 或子派发。只读使用 canonical Python 环境不等于在 main 开发。

剩余边界保持：观察器 commit 前易失、插件订阅 best effort、保留期有界去重、effect 与账本非原子、不可证明的副作用留 uncertain。没有承诺可靠订阅/exactly-once/真实第三方幂等，也没有扩大 Job 功能或增加前端规划。

交付后停止，由 MA 对冻结 SHA/最终证据提交差异、六项复现及 main 数据兼容门禁独立验收。practical 暂不移动。
