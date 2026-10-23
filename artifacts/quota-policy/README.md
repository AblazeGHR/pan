# V0.3 额度策略基础后端候选

本批在原 core 隔离树续作；候选交付不等于 MA 接受或真实供应商验收。

- 工作树：`D:/project/pan-worktrees/event-trigger-core-20261022`
- 分支：`feature/event-trigger-core-20261022`
- 起点：`d77d0b6a7c15595883ebd2a17046fca3cc3c1e53`
- 功能提交：`0d77f414c7625faa3bad0bd35ac4b2139e0d6e82`
- **最终精确受测源码：`e7879afc6929b6e71a14713921d8d937df118a39`**
- 后续提交仅保存本目录证据。源码、契约和测试均在上述源码 SHA 内。

## 已实现及证据矩阵

| 要求 | 本批行为 | 可执行证据 / 边界 |
| --- | --- | --- |
| 最新真实 quota 样本 | 从授权 JSONL 只读提取真实 `usageLimitExceeded`，保留原 provider error 形状、null 和关联字段；与 `serverOverloaded` 独立分类 | `tests/fixtures/runtime_issues/provenance.json`，`test_actual_latest_quota_and_capacity_fixture_keep_native_fields` |
| 严格 parser / 裁剪 | 结构化枚举优先；泛化 quota/capacity 文本及 tool failure 为 unknown；元数据仅标量白名单；裁剪凭据字段及内嵌 token | `tests/test_runtime_issues.py`、`test_generic_quota_capacity_and_tool_output_never_drive_policy` |
| 统一消费与关联 | 可信内部 RuntimeIssue observer，有界 128；按 Session/Worker/generation/operation/classification 去重，错误帧与终态合并 | `test_logged_native_issue_enters_bounded_policy_observer`、`test_concurrent_duplicate_noop_revision_and_missing_account_identity` |
| 持久恢复状态 | 独立原子 JSON 保存 revision/epoch、case、排除、重试账户、command/receipt、额度和 Job 引用；写盘 off-loop | `test_uncertain_receipt_and_restart_never_repeat_side_effects`、`test_default_adapter_fail_closed_and_snapshot_write_failure_no_effect` |
| CBC 429 | 同 Session/Worker/generation 的固定 `cbc_next_model` 命令；不猜 fallback 模型 | `test_cbc_keeps_session_and_does_not_query_accounts`；生产 adapter 默认 unavailable |
| capacity 一次重试 / 账号排序 | 当前五小时和周均有余额才发一次继续；确认后再次 capacity 才排除；按最近周重置、周使用率、五小时使用率、ID 排序 | `test_capacity_retry_once_correlates_terminal_then_switches_and_orders_restarts` |
| 账号 / Worker 绑定 | 必须证明事件 Worker 使用的账号等于实际账号；过期 generation 或查询期间账号变更不能切换 | `test_unknown_or_stale_or_old_worker_never_switches`，并发去重测试 |
| 重启 / 唤醒顺序 | 冻结存活 Codex 清单，先非 MA Worker，原 running TA 逐一唤醒，建立唯一监控和确认 MA 唤醒 Job，最后 MA；idle Codex 包含在重启清单，CBC 排除 | 顺序测试；只有 fake adapter 验证了效果回执，不是实际账号切换或 Worker 重启 |
| 交接期间新 capacity | 持久记录新 case，阻断待执行 MA 步骤；不猜测完整 MA 再次换号循环 | `test_new_capacity_during_handover_blocks_ma_steps_and_restart_dedup`；完整现场编排待后续 |
| 持久额度查询 Job | 复用通用 Job registry、schedule、run ledger、scheduler tick；一次查询和每 60 秒 95% 监控；同 epoch/purpose/account 幂等 | `test_monitor_job_persistent_unique_95_and_manual_late_notice_fenced`、两个并发 Job 测试、真实 HTTP |
| 五小时 95% | 绑定实际账号，fresh >=95 后停止监控，仅规划一次 MA 提醒；换账号取消旧监控 | 阈值测试；自动收到提醒后切换的 MA 完整闭环未实现 |
| 全耗尽最近重置 | 选择两窗口可同时恢复的最近可核实未来时间，创建一次 reset 查询 Job；保留阻塞 Worker 清单；先刷新后唤醒 | `test_all_exhausted_has_one_durable_reset_query_job_and_unknown_is_not_threshold` |
| 查询失败 / stale | 显式 unknown，不能解释为零额度或达到阈值，不切换；未知账号不能建立绑定监控 | normalize、未知额度测试及真实 HTTP |
| 手动 / 授权 | 默认 manual；修改和创建 Job 要用户 Bearer + expectedRevision；手动 epoch fence 迟到查询和通知 | `test_manual_change_during_monitor_query_cannot_create_or_wake`、真实 HTTP 401/400/409 |
| 旧入口安全 | quota-query 受保护，旧 Job PATCH/delete/run-now 及 scheduler aliases 不能修改或重投 | 真实 HTTP 三个拒绝断言；`final-compat.xml` 中 Action Job / 调度兼容用例 |
| 关闭 / uncertain | 关闭后不新 admit/dispatch；等待当前 bounded tick 收敛再释放 profile owner；已开始效果记回执；恢复 dispatching→uncertain，禁止盲重放 | `close-before-fix.xml` / `close-after-fix.xml`，两个关闭回归 |
| 有界账本 | seen/cases/commands/excluded/capacityRetried 各 4096，超限拒绝且保留已有去重记录；诊断入口有界 | `test_ledger_capacity_rejects_without_erasing_dedup`；归档/解除排除 API 未实现 |
| 原契约共存 | 保留 query 冻结/恢复、Group 与高风险门禁、有限重试、插件 generation / queued 恢复、背景与消息 Job | `final-compat.xml` 66 项；没有机械重跑全仓或历史所有批次 |

## Fixture 出处

源文件 `D:/project/Pan/data/logs/session-errors.jsonl` 只读；本轮交付前再核对，最新匹配条目未变。

| 类别 | 原始行 / eventId | 原行 SHA256（不含换行） |
| --- | --- | --- |
| quota exhausted | 508 / `se_9434f64c9f4055ab9a79f92bcf55e77b` | `9096d900be3679e0b9686bfb7f5362b3b40c05fa0a510b8f2f55405466052873` |
| capacity | 551 / `se_1f8e146499db5277a1c9162d32e8ccd4` | `75accaf1cd47ece5f6016d85843d4cc44f2f10d03a4b60ed65afbc04aaa71ac1` |

删除的是用户历史、raw diagnostics、无关 usage；不重写 provider error，也未添加伪造 error code。
原样本 native error 中没有凭据。以后源文件追加不改变本 fixture 的取证哈希。

## 冻结源码验证

入口：

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe scripts/verify_quota_policy_candidate.py
```

所有 pytest 经 `scripts/run_tests_isolated.py -- ...`，完整 argv、耗时、退出码和源码 SHA 在 [verification.json](verification.json)。

| 检查 | 结果 | 退出码 / 证据 |
| --- | --- | --- |
| 核心 / parser / 日志 / 并发 | 87 passed，0 skipped | 0；[JUnit](final-core.xml)、[日志](final-core.log) |
| Job / 调度 / query / retry / plugin 选定兼容 | 66 passed，0 skipped | 0；[JUnit](final-compat.xml)、[日志](final-compat.log) |
| 真实隔离 HTTP | 1 passed，0 skipped | 0；[JUnit](final-http.xml)、[日志](final-http.log) |
| compileall / diff --check | 通过 | 均 0；[compile](compile.log)、[diff](diff-check.log) |

**最终合计 154 passed，0 failed/errors/skipped。** 两条 Starlette/httpx 依赖弃用 warning 保留在兼容日志中。
Windows 原生默认环境：UTF8 mode 0，stdout GBK，locale cp936；子进程未设置 PYTHONUTF8 / PYTHONIOENCODING。
证据文本显式 UTF-8；subprocess 输出显式 decode，若无法解码保留原 bytes，不改全局环境。

## 修前失败证据

- `first.xml`：19 项中 2 失败，新 Job 工厂遗漏 `_misfire`，已补齐现有通用 Job 必填参数。
- `http-first.xml`：真实 HTTP 1 失败，无变化事务仍增加 revision 导致 expectedRevision 冲突；已修为无变化不写盘。
- `observer-preflight.xml`：16 项中 1 失败，新增测试漏填必需 `terminal`，修正 harness；未削弱业务断言。
- `close-before-fix.xml`：在 `0d77f414...` 上仅添加屏障回归，1 失败，证明关闭期间迟到查询仍派发 switch；`e7879afc...` 修复。
- [failure-index.json](failure-index.json) 记录上述退出码、关闭复现完整命令及证据保留限制。早期未冻结探索命令没有完整 argv 留档，不冒充最终精确源码测试。
- `pre-shutdown-fix/` 保留初次冻结 0d77f414 的 manifest/JUnit/HTTP；其 152 项不叠加计入最终 154 项。
- 其余 preflight JUnit 和早期 Job Action HTTP 兼容记录作为辅助证据，不替代最终 SHA 的验证。

## API、迁移和未完成边界

稳定接口和数据/adapter 责任见 [quota-policy-v0.3.md](../../docs/design/quota-policy-v0.3.md)。

- GET `/api/automation/quota-policy`：safe state、unknown/fresh quota、排序、命令结果、Job 引用、诊断。
- PUT 同路径：用户 Bearer，严格 `{mode,maSessionId,expectedRevision}`。
- POST `/api/automation/quota-policy/query-jobs`：用户 Bearer，严格 `{kind:"monitor95"|"once",expectedRevision}`。
- schema 入口 `/api/automation/schema` 增加 quotaPolicy 描述。没有公开事件注入、账号快照、adapter 安装或效果回执接口。

新增文件默认 manual/unknown，不迁移或重放历史 JSONL；重启只恢复已提交策略状态。
JSONL→内存 observer→policy transaction 的提交前窗口是**易失**的；没有声称已经接入持久 Event Store 或保证该段 at-least-once。
query Job 文件与 policy ledger 是分别原子文件；确定性 Job ID 支持重复请求修补引用，epoch 防过期效果，不能声称跨文件原子。
旧版本不认识 quota_query 的混合写入不受支持；停机只读备份/审核新增 Job 后再降级，不能让旧调度器猜测执行新类型。

生产 adapter 默认 unavailable。本批提供可测试、有限等待、fail-closed 的 query/inventory/perform 接口；没有已验证的真实 managed-account 五小时/周额度与账号/Worker generation 协调回执链，因此未猜测真实切换。
真实账号切换、CBC 模型 fallback、重启所有 Codex Worker、MA/TA 唤醒、提醒后再次换号的现场闭环和交接途中 capacity 的完整 MA 重入循环均**未实装/未真人验收**。unknown 回执自动 reconciliation、归档/unexclude API、分布式 owner 也未实现。
容量临时排除和一次重试账户历史不会因自动/手动切换偷偷清空；需要后续显式复核/解除入口。
通用 Job 的长期保留继续使用既有策略，本批没有替换其归档系统。

## 清理和边界

最终 [HTTP evidence](http-evidence.json)：自有临时 profile、端口 64801、PID 2672；`ownedServerStopped=true`、`portReleased=true`。
该 HTTP 测试没有创建 Worker、调用供应商、切换账号或重启 Worker。早期通用 Job Action/fakeCLI 兼容证据在 `job-action-compat-http.json`，不混入本次源码实测计数。
真实 provider/model/query calls=0，真实账号 switch=0，既有服务操作=0。
未操作 8767/8768、真实 Session/data 写入、收费模型、共享依赖、React；没有子派发。
没有移动 main/practical/集成树，没有 push/release。提交 clean 候选后停止，等待 MA 独立复审；整体人工验收仍待用户。
