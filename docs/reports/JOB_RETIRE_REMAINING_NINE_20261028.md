# JOB-RETIRE revision3：剩余九节点证据与最小协调范围

本批基线 `9cf449ac4d2149ba6ae900683ce8b26b45b98828`，分支 `r6/retirement-closure-20261027`，工作树 `D:/project/pan-worktrees/job-retirement-closure-20261027`。用户告知该基线已由 MA 复跑 3/3、合入 integration `52ce2fc5`；本批没有切换或推进 integration。

**本批关闭 0 个 gap，九项全部保持 gap。** 新增三条可重复的提交前校验红证据，以及三个通过的真实边界测试。通过项分别证明真实 Worker provider 的不存在 Session 拒绝、无 channel provider 时的持久失败与重复 dispatch 不变、quota 真实 TCP 的 CAS/鉴权/持久化和 legacy writer fence；没有一项被冒充为完整原语义严格后继。

## 逐节点结论

完整 node ID、测试 node ID、失败/通过状态和共享符号见 [逐节点表](../../evidence/job-retirement-closure/remaining-nine-review-20261028.md)，机器证据见 [gap-evidence](../../evidence/job-retirement-closure/gap-evidence-20261028.json)。以下各项原节点均仍为 gap，`strictSuccessors` 为空：

| 原节点（除特别标注均属 `tests/test_jobs_api.py`） | 本批证据 | 仍缺的严格语义 |
|---|---|---|
| `test_resume_legal_running_job_rescans_and_run_now_sends_only_to_current_candidates` | 源码核对；没有执行效果证明 | canonical 产品 Action 和 typed executor 没有 legal-offline 重扫/resume 端口。现有 dynamic `session_operation` 调用 pause，不能证明恢复。需真实 Session 候选变化与发送效果、legal/Wake 边界和冻结后的身份。 |
| `test_resume_legal_running_empty_scan_is_successful_noop` | 源码核对；没有执行效果证明 | 缺同一 native resume 端口的零匹配 completed receipt。broadcast 要求非空 IDs，不能放宽该契约来代替。 |
| `test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields` | **红**：固定 `targetId=not-a-qq-id`，真实 canonical POST 返回 201、定义与 operation 已提交 | 写前拒绝；完整合法 target/bot 持久化和原非法字段矩阵仍未证明。 |
| `test_patch_scheduled_qq_action_is_strict_and_editable` | **红**：相同非法固定 target 的 PUT 返回 200、revision=2 | 写前拒绝及失败不写；合法编辑与历史 frozen Run 不变的完整 QQ 专属后继。失败处之后的 no-write 断言没有执行，未声称通过。 |
| `test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure` | 支持测试通过：实际 TypedExecutor 无 channel provider 时记录 `provider_unavailable`，Run failed、一次 Attempt、Event 统计不变，第二次 dispatch 状态逐字典相等 | 生产未绑定 channel provider；尚无真实 QQ plugin 错误 receipt。测试专属 channel Action 未连接插件，不算该原节点后继。 |
| `test_scheduled_qq_backlog_retry_runs_without_session_target` | 没有严格运行证据 | 原 effect 的 provider receipt/query 和可证明的 rejected retry；unknown 只查询，不能重投。不能修改测试 Action 的 retry/proof 来制造通过。 |
| `test_post_requires_existing_session` | **红**：不存在的固定 Session 在 POST 返回 201；另一个真实 WorkerSessionProvider 测试通过，执行失败且不新增 Session/Worker、Session 文件字节不变 | 执行时拒绝不能代替写前业务校验；还需存在的 Session 正向路径。 |
| `tests/test_authorization_runtime.py::test_automatic_recovery_revocation_keeps_budget_and_never_starts_another_attempt` | 源码核对；没有预算/撤权效果证明 | `_references` 对 automatic=true 明确拒绝未绑定策略；缺实际绑定策略、撤权、预算原子性与无第二 Attempt。保持 unsupported 防线。 |
| `tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http` | 支持测试通过：自有 loopback 随机端口真实 TCP；401/200/409 CAS；once/monitor95 均被 active owner fence 为 409；无 query command/Job 副作用 | 缺 native query admission 与 unavailable adapter 的 unknown 终态。listener 重开及新 Store 读回不等于完整进程/lifespan startup/reopen。 |

## 需要 MA 协调的最小共享范围

本批**没有修改任何共享产品符号**。以下仅为精确协调建议，不构成已获批实现设计：

1. 最小且已有稳定红证据的修复：`packages/jobs/final_contracts.py::_check_job_references` 与 `packages/jobs/final_service.py::FinalService._references`。在现有定义事务内校验 fully literal 的 typed Action arguments 对应不可变引用的 `inputSchema`；动态 input/Event/step bindings 仍在解析时校验。对 `product_session_message` 等固定 Session literal 增加只读存在性校验，不启动 Worker。POST/PUT 同用已有 CAS，失败不能写 operation/definition。实施前由 MA 确认真正所需符号，避免两处重复实现。
2. 优先 resume 两项：`FinalService._install_product_actions`、`packages/actions/typed_executor.py::TypedExecutor.execute` 需要 MA 确定明确 native query/resume 能力及返回契约。候选选择与执行前 legal/Wake 复核必须使用已有真实边界。`packages/actions/executor.py::execute_dynamic` 仅是现有 pause 行为的核对位置，**不建议把 pause 改成 resume**。`TypedExecutor._session_broadcast` 的非空约束保持。
3. QQ：`FinalService.__init__` / `_install_product_actions`、`TypedExecutor._channel` 缺生产可信 channel provider 绑定；`FinalService.control_run` 涉及 retry proof。先由 MA 确定真实 plugin transport、bot 权威、版本化 receipt/idempotency 查询和明确拒绝的清理证明，再增加真实自有 HTTP fixture。不能绕开 unknown fence，也不能沿用 provider double 关闭节点。
4. automatic recovery：`FinalService._references` / `_guard_effect` 缺 separately bound recovery policy 的实现；需要独立策略/预算事务设计，不属于去掉 unsupported 检查的小修。
5. quota：`packages/automation/api.py::install.quota_query_job` 与 `packages/automation/quota_policy.py::Runtime.create_monitor/ensure_job/_ensure_job` 仍指向已被 fencing 的旧 Job admission。需 MA 协调 native Action/Job/Creator admission 并保留原 policy identity。只修历史 fixture 的 NameError 仍不能证明 native unknown query，也不能恢复 v1 writer。

未提议修改 Worker、FinalService 以外的 server 生命周期、三个 scheduler hosts、process owner 或 CI。任何新增授权仍需明确范围。

## 本批实测命令与结果

以下均由 `D:/project/Pan-main/.venv/Scripts/python.exe evidence/job-retirement-closure/run_matrix.py <label> <paths>` 发起；该工具记录完整实际命令，并调用 `scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest ... -q --tb=short --junitxml=...`。所有路径都是本树相对路径。

| label / paths | pass / fail / error / skip | 实际 exit | 用途 |
|---|---|---|---|
| `provider-boundaries-first` / `tests/test_job_retirement_provider_boundaries.py` | 1 / 4 / 0 / 0 | 1 | 保留首轮失败实验：三条产品语义红，以及测试错误 `worker._workers`；已更正为实际 `worker.workers`。 |
| `provider-boundaries-final` / 同上 | 2 / 3 / 0 / 0 | 1 | 校正后稳定三红。此历史轮次使用既有 h fixture；未用其 recording provider 执行相关 effect。 |
| `quota-transport` / `tests/test_job_retirement_quota_transport.py` | 1 / 0 / 0 / 0 | 0 | 首次真实 TCP 边界。 |
| **`remaining-nine-native`** / 上述两个测试文件 | **3 / 3 / 0 / 0** | **1** | 最终有效矩阵；改用专属 `tests/job_retirement_native_fixture.py`，默认产品 executor、真实 worker、原产品 retry/proof，无 provider double、无测试修改 retry。 |
| `remaining-nine-mapping` / `tests/test_job_retirement_closure_gate.py` | 5 / 0 / 0 / 0 | 0 | 映射与 inventory 工具一致性；不是效果、清理或退役证明。 |

每轮的 `<label>-command.json`、`<label>.xml`、`<label>.log` 均保留在 `evidence/job-retirement-closure/`。最终命令入口：[native command](../../evidence/job-retirement-closure/remaining-nine-native-command.json)，[JUnit](../../evidence/job-retirement-closure/remaining-nine-native.xml)，[原始输出](../../evidence/job-retirement-closure/remaining-nine-native.log)。JSON 中包含 source SHA256 与实际节点结果。fixture 从库级隔离获得临时路径，测试从未启动 Worker，也未使用真实账号或插件。

`record_gap_evidence.py` 仅在实际 command 节点结果与 red/support 预期一致时记录证据；`build_mapping.py` 重建时加载该 dated overlay，强制九项仍为 gap。没有 gate 固定结果被记作严格后继。

## 保留项与未验证层

原 43 节点、P23 原三红以及名称原红证据均保留。原映射 33 个 strict-successor 的状态与后继列表没有调整或回退；本轮没有重跑它们，不能将历史计数记入本批。另一个 `product-gap` 是名称修复前的历史分类，本轮沿用原字段；它不表示已集成名称修复被撤销。本批仅为九个 gap 增加当前核对和证据，不宣称整体迁移、删除或退役完成。

未验证真实 QQ/plugin 消费、resume 候选变化/恢复效果、automatic recovery 绑定、quota query unknown 终态、完整服务进程 startup/reopen、浏览器/三平台/远端 CI、真实 51/60 cohort 身份与迁移、部署及人工验收。未读取真实集合，没有 unknown replay。没有改 main/practical、push、远端 CI、WSL、既有服务或 8767/8768。自有 listener 退出时均检查线程结束与原端口不可连接。

可执行下一步：MA 先针对上述三条写前校验红确认最小共享符号；独立协调 native resume 端口后，以真实临时 Session/Worker provider 及不绕过 Wake 的端到端效果补齐优先两节点；channel 绑定和 receipt/query 契约明确后再补 QQ plugin failure/backlog。候选为含预期红测试的诊断交付，须按命令和证据验收，不能当全绿产品修复。
