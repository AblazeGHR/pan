# JOB-RETIRE revision8：写前校验修复的连续收尾

原分支 `r6/retirement-parity-acceptance-20261029-a`、原工作树保持不变；起始 clean HEAD 为 `877717b0014ed386c6bafbfa3c3443f53aa5f309`。本次获准的 fixture 修正源码提交为 **`0dc3cbad7f86c6553dfcfc94032dd5e864749e44`**，产品与 revision7 完全相同。证据提交只新增本版报告/证据。

## 唯一源码改动

`tests/test_session_product_closure.py` 仅在两个指定函数增加 12 行，没有删除或修改原代码：

- `test_product_missing_target_rejects_without_spawn`
- `test_product_fanout_partial_unknown_cold_reopen_never_resends`

两个测试均用真实 `sessions.save(Session(id="ses_missing", ...))` 在创建 Job 前建立临时目标；`launch` 返回 admission 后，先断言目标仍存在，再调用真实 `sessions.delete("ses_missing")`，断言 effect 之前目标不存在，随后才调用原 `dispatch_once`。未改全局 fixture、conftest 或 storage lookup，没有关闭写前校验。

原运行期断言逐字保留：同一 missing ID、failed、partial/unknown、逐目标 outcome、调用顺序、冷重开原 ID 查询、no-resend。新增 Worker registry 不变断言。源码/断言审计及真实删除时序在 [finish-matrix.json](../../evidence/job-retirement-rev8-20261029/finish-matrix.json)，源码差异在 `fixture-timing.diff`。

这是生命周期时点纠正：revision7 恢复了原写前存在性契约，原 fixture 在创建阶段预置 missing target 的方式就不能再抵达运行期异常场景。现在保持“通过写前检查 → admission → 目标消失 → 原业务执行”的真实竞态，未把 rejected/partial/unknown 解释为成功，也未改变原 provider fault 行为。

## 同一不可变 SHA 的组合矩阵

使用 `D:/project/Pan-main/.venv/Scripts/python.exe`，通过 `scripts/run_tests_isolated.py`，单一 pytest 进程选择以下八组：

```text
tests/test_retirement_rev7_write_validation.py
tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin
tests/test_retirement_rev6_session_validation.py
tests/test_job_retirement_literal_validation.py
tests/test_job_retirement_name_scope.py
tests/test_session_product_closure.py
tests/test_job_final_http_service.py
tests/test_scheduler_time_templates.py
```

此选择是原 131 项矩阵与前次 64 项 affected-http 矩阵的完整并集；两条重合的 Template 环境节点只执行一次。共 **193 个不重复节点**，其中整个 `session_product_closure` 为 12 个节点。实际结果与退出码保存在 [组合命令记录](../../evidence/job-retirement-rev8-20261029/combined-immutable-command.json)、[JUnit](../../evidence/job-retirement-rev8-20261029/combined-immutable.xml)和[原始日志](../../evidence/job-retirement-rev8-20261029/combined-immutable.log)。`build_finish_evidence.py` 强制检查原两组全部 node ID 都在本次 passed 集合内，源码 SHA 必须一致，不允许重复计数或静默丢节点。

实测结果：**193 passed / 0 failed / 0 error / 0 skipped，exit 0，157.937 秒**。原两条红的运行期断言均已实际到达并通过，未发生 skip/xfail、断言放宽或 fixture/mock 绕过存储。原有 fanout unknown fault 保持原样。

指定解释器仍为 Python 3.14.5，tzlocal 5.4.5、tzdata 2026.5；本次没有安装或修改依赖、OS timezone、产品 fallback。依赖版本随 finish-matrix 记录。

## 历史证据与剩余范围

revision7 的原两红 `affected-http.xml/.log/-command.json` 完整保留，并在 finish-matrix 中登记 hash 和逐 node red→green 对照。原八条写前产品红、revision6 原始映射/ZIP 以及 MA 证据也没有覆盖或重新分类。

本批只有 fixture timing 修正。QQ retry、default assign/shell form wrappers、MCP compatibility、monitoring/stable-reset、unknown fallback、clear-target 决策仍开放；本批不补这些实现，也不声明整体迁移完成。

自有测试仅使用隔离 Sessions、临时存储和随机端口；QQ/Session fixture teardown 保留线程结束、socket 释放及原存储关闭。没有启动真实 Worker、访问真实账号/profile/51/60 集合、操作既有服务或 8767/8768，也没有 main/practical、push、远端 CI、WSL、部署或浏览器动作。TA 候选仍需 MA 独立验收整合和 target CI。

本版 `raw-artifacts.zip` 保存证据和受测测试文件的原始 bytes；`raw-archive.json` 保存包 hash，避免 Git 文本换行归一化影响原 bytes 的核验。
