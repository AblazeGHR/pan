# JOB-RETIRE revision7：已知写前校验回归

基线 `14380fe47b01d1adbaf69bfc43983b5df7947a80` 已核实 clean；保持工作树 `D:/project/pan-worktrees/job-retirement-closure-20261027`、分支 `r6/retirement-parity-acceptance-20261029-a`。产品与专属测试源码固定提交为 **`4ef9764c9089a714b9355c5932f0060a68719326`**，随后在该不可变 SHA 上运行最终矩阵。本批证据提交只增加报告/证据，不改变该产品源码。

## 产品范围

仅修改 `packages/jobs/final_service.py`，AST 比较确认改变的符号只有：

1. `FinalService._check_literal_product_sessions`：保留 v1，显式增加 `product_session_message_v2` / `session-message` / `sessionId`、`product_session_broadcast_v2` / `session-broadcast` / `sessionIds` 和 `product_assign_v2` / `assign` / `sessionId`。保留实际 system publication、operation、已解析 inputSchema 和 shallow Session lookup 条件。
2. `FinalService._references`：在原事务和引用校验位置调用 QQ helper，没有新增事务或执行层。
3. 新增纯 helper `FinalService._check_literal_product_qq`：仅匹配已解析 immutable `product_channel`、system=true、原 pan.typed.v2 provider release/generation、operation=channel。已知 literal targetType/targetId/botUin 复用 `normalize_scheduled_qq_action`；literal text 纯空白拒绝，合法文本原文不修改。

部分动态绑定时，只投影已知 literal 给原 normalizer。normalizer 的必填但未解析字段使用合法校验上下文值；这些值不写回 arguments、定义、冻结计划或效果，也不选择目标。动态字段保留原 input/Event/Step 绑定与现有解析时校验。custom channel/Session 参数名不获得产品业务约束。

没有修改 Action publication、schema、Worker、transport、recovery、quota、MCP、三个 scheduler host、shared tests/conftest/support 或 CI。源码 diff 见 [product.diff](../../evidence/job-retirement-rev7-20261029/product.diff)。

### assign 固定目标契约的依据

读取退场前 `68be5bd4^` 的 `packages/jobs/api.py`：原 scheduled-task 创建在有 sid 时调用 `_session_exists`，原编辑对 `action_api in {assign, send_session}` 明确要求固定目标存在。原 `test_post_requires_existing_session` 使用默认 scheduled action，原 `test_action_template_default_assign` 确认默认 action 为 assign。证据保存于 [original-assign-contract.json](../../evidence/job-retirement-rev7-20261029/original-assign-contract.json)，包含解析后的完整旧 source SHA、行号、hash 与原检查片段。

专属测试验证 source-aware message/broadcast/assign 的实际存在目标创建/编辑、CAS、冻结 Run、不改 Session bytes、不 spawn、Session.get 只传 load_history=False；另有 assign POST/PUT missing-target 的 SQL/operation/revision/no-spawn 回归。因此纳入 assign 是恢复原固定目标写前契约，不是从自定义字段名推断新要求。

## 原八红原样转绿

`tests/test_retirement_rev6_parity.py` 与 `tests/test_retirement_rev6_session_validation.py` 相对基线未修改。原 rev6 XML/log/JSON 和 raw ZIP 未覆盖；MA 的 `parity-r6-ma-red.xml` 仅记录 hash，不重写。

| 原红节点（精确参数） | 原 rev6 / MA | `4ef9764c` 最终结果 |
|---|---|---|
| `test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[targetId-123-create]` | fail，POST 201 | pass，400，无写入 |
| 同函数 `[targetId-123-update]` | fail，PUT 200 | pass，400，无 revision/operation 写入 |
| 同函数 `[text-  -create]` | fail，POST 201 | pass，400，无写入 |
| 同函数 `[text-  -update]` | fail，PUT 200 | pass，400，无写入 |
| `test_current_source_aware_product_missing_fixed_session_rejected_before_commit[session_message-create]` | fail | pass |
| 同函数 `[session_message-update]` | fail | pass |
| 同函数 `[session_broadcast-create]` | fail | pass |
| 同函数 `[session_broadcast-update]` | fail | pass |

机器逐 node red→green 映射：[validation-matrix.json](../../evidence/job-retirement-rev7-20261029/validation-matrix.json)。它只为原 43 节点中的 QQ create/edit、existing Session 增加 **write-validation-proven** 证据，不宣布整个 M03/M04/M08、原九项或整体迁移完成；其他 rev6 语义与状态仅引用为历史证据，未伪造本轮复跑。

## 指定 venv 依赖

使用 `D:/project/Pan-main/.venv/Scripts/python.exe -m pip install 'tzlocal>=5.3,<6' --report ...`，安装的是已在 `minimal-requirements.txt:11` 声明的包。

- Python **3.14.5**，pip **26.2.1**，prefix 为指定 `.venv`；没有 base/user 安装。
- 新安装 **tzlocal 5.4.5**；tzdata **2026.5** 已存在，未新增其他包。
- wheel 来源：`files.pythonhosted.org`；SHA256 **`ecab534f6d65d9deb4c4ea52c121ab6dd5958e9b878e71dcb102578269752454`**。
- 未升级 pip，未改 OS timezone 或产品 fallback；host-zone resolver 安装后可正常解析。

实际 argv/exit/stdout/stderr、pip download metadata 和解释器版本见本版 `pip-install.json`、`pip-report.json`、`interpreter-dependency.json`。原两条 `test_scheduler_time_templates.py` 环境红原样重跑通过；不是断言或时区配置被改绿。

## 命令与实际结果

每组由指定解释器运行 `evidence/job-retirement-rev7-20261029/run_matrix.py <label> <nodes>`，内部命令为 `scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest ... -q --tb=short --junitxml=...`。完整 argv/cwd/count/exit/source SHA 在各 `<label>-command.json`，原结果在同名 XML/log。

| label | 实际 pass / fail / error / skip | exit | 范围 |
|---|---:|---:|---|
| unchanged-red-green | 20 / 0 / 0 / 0 | 0 | 修复工作树首次原样复跑原字段 16 例 + Session 4 例 |
| dedicated-first | 25 / 0 / 0 / 0 | 0 | 修复工作树新增专属边界回归 |
| environment-green | 2 / 0 / 0 / 0 | 0 | 声明依赖安装后的原两条环境红 |
| **immutable-final** | **131 / 0 / 0 / 0** | **0** | 受测 SHA `4ef9764c`：上述原样矩阵、新增 25 例、两条环境原节点，以及 literal/schema/name/frozen/CAS 回归 |
| **affected-http** | **62 / 2 / 0 / 0** | **1** | 同一受测 SHA：完整现有 Final HTTP、time Template、Session product closure 三文件 |

最终不可变矩阵入口：[command](../../evidence/job-retirement-rev7-20261029/immutable-final-command.json)、[JUnit](../../evidence/job-retirement-rev7-20261029/immutable-final.xml)、[log](../../evidence/job-retirement-rev7-20261029/immutable-final.log)。没有把不同矩阵的重复节点相加为完成率。

## 两条受影响共享 fixture：保留红，待 MA 最小协调

以下两个既有节点仍原样保留，未 skip 或降低断言：

1. `tests/test_session_product_closure.py::test_product_missing_target_rejects_without_spawn` 在创建 assign Job 时直接使用 `ses_missing`，仍期待 201，因恢复的写前存在性要求得到 400。
2. `tests/test_session_product_closure.py::test_product_fanout_partial_unknown_cold_reopen_never_resends` 在创建 broadcast 时包含 `ses_missing`，同样在 400 处失败，尚未到它要验证的 runtime partial/unknown 场景。

这两条不能报告为本批全绿，也不能为通过它们取消已授权写前检查。请求 MA 协调的精确最小测试范围：仅在这两个函数中先建立真实临时目标，完成 admission 后再删除该自有目标；保留现有 runtime rejected/partial/unknown/no-spawn/cold-query/no-resend 的全部断言和 provider 行为。本批 shared tests 仍只读，原失败 trace 在 `affected-http.xml/.log`。新增专属正向/no-write 测试和受影响 HTTP 62 项通过不能替代这两条后续断言。

## 不在本批关闭的范围

QQ known-failure retry、default assign/shell form wrappers、旧 MCP compatibility、monitoring/stable-reset、unknown fallback 和 clear-target 决策维持 rev6 显式待办。没有新账本或 provider、没有更改重试/发布/recovery/执行行为。动态参数的原运行时验证仍由原路径负责，本批不扩大 provider 可重投能力。

自有 QQ plugin fixtures 仅使用未连接的临时 channels 和随机 loopback 端口，结束时检查线程退出、socket 释放、原端口不可连接；验证测试不发送真实 QQ，不启动 Worker。真实账号、profile、51/60 数据、既有服务、8767/8768、main/practical、push、远端 CI、WSL 或部署均未操作。无前端修改、浏览器或真实迁移验收。

此交付是有界写前校验候选，包含 131 项全绿最终矩阵及 2 条未修共享 fixture 红；需要 MA 独立验收和整合，不等同整体验收或 target CI 已通过。
