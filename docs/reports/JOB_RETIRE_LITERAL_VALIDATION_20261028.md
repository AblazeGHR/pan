# JOB-RETIRE revision3：最小写前校验收尾

本批基于 `fdf3d51a259f622ce6a3d3b41e230d701dc75eb3`，只实现 MA 授权的两个范围：

- `packages/jobs/final_contracts.py::_check_job_references` 对 fully-literal Action arguments 复用 `_validate_instance` 和已解析的不可变 Action `inputSchema`；只要存在 input/event/step/system 动态绑定，就保留现有解析时完整对象校验。
- `packages/jobs/final_service.py::FinalService._references` 新增 `_check_literal_product_sessions`，仅识别两个已发布产品 Action ID 和对应 operation：`product_session_message`/`session-message` 的 `sessionId`，`product_session_broadcast`/`session-broadcast` 的 `sessionIds`。固定目标使用 `session.get(..., load_history=False)` 只读检查，不启动 Worker；不存在或 schema 不符时在事务内拒绝，operation、definition、revision 均不落盘。

名称语义已收窄：`_validate_name_semantics` 只检查 canonical 对象的声明名，以及 Template 内嵌的 Job、create-mode Event Creator、Trigger 定义；不再递归任意 arguments、literal、payload、schema default 或 config 中的 `name`。合法中英文和前后空白保持原文。

专属回归位于 `tests/test_job_retirement_literal_validation.py`、`tests/test_job_retirement_name_scope.py`；原 `tests/test_job_retirement_provider_boundaries.py` 三红节点保持原断言和证据。最新命令：

```text
D:/project/Pan-main/.venv/Scripts/python.exe evidence/job-retirement-closure/run_matrix.py literal-name-focus tests/test_job_retirement_name_scope.py tests/test_job_retirement_literal_validation.py tests/test_job_retirement_provider_boundaries.py tests/test_job_final_contracts.py
```

结果：**218 passed, 0 failed, 0 errors, 0 skipped; exit 0**。实际命令、JUnit 和日志见 `evidence/job-retirement-closure/literal-name-focus-command.json`、`literal-name-focus.xml`、`literal-name-focus.log`。首轮 150/9 和扩展 224/1 的失败证据也保留；首轮错误是测试读取公开视图时误取隐藏 `plans`，扩展矩阵的单个既有权限测试使用了未创建的固定 `owned-test-session`，没有删除或改写这些日志。

回归明确覆盖：五种 canonical kind 顶层名、Template 内嵌 canonical 名、合法原文不 strip、任意业务 JSON 的空白 `name`、Action schema 对 payload 的判定、合法 Session message/broadcast 创建编辑、固定 QQ schema、动态绑定延迟校验、冻结 Run 不变、失败请求不写 operation/definition/revision、真实 Session 文件只读检查、无 Worker spawn，以及 system grant/high-risk fence。

原九项 gap 仍全部开放；本批没有修改 Action publication、native resume、channel provider、automatic recovery、quota、三个 scheduler host 或 successors 状态，也没有宣称整体迁移/退役完成。未触碰真实 profile、51/60 集合、既有服务、8767/8768、main/practical、远端 CI、WSL 或 push。
