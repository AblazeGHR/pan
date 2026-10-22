# User Inbox / Interaction Card 后续 TA 候选

## Git 与范围

- 工作树：D:/project/pan-worktrees/user-inbox-interaction-contract-20261023
- 分支：feature/user-inbox-interaction-contract-20261023
- 精确起点：7446efcb55545be35e76b4845941991202b8cd93
- 精确源码SHA：f2048c781f91583e2c30af11e57548fc78de2e60
- 后续提交只记录证据，不修改packages/tests/docs源码。交付后停止，由MA独立验收。
- 原 Agent Inbox 工作树保持clean；未操作main/practical、push/release或真实服务。没有合入其他TA的Job/Trigger候选或未提交改动。

## 具体交付

新增 packages/interactions：严格schema、User Inbox SQLite、一次性response/Event事务、Job快照兼容投影、定向Trigger适配器及HTTP入口。MCP新增 interaction_card(create/get)生产者工具，按原caller self/managed权限限定owner；没有用户respond/dispatch工具。

Card支持可选answerInput、可选choice triggerRef、通知title/summary/tag元数据。没有控件的Card只读，不隐式同意；answer和choice可显式同时携带，不增加未确认的必选/默认回答/超时规则。owner+requestKey幂等，card+recipient+requestId答复幂等，冲突明确拒绝；读取不写Session context，不改变Agent Inbox/Locker消费。

User Inbox用户端每次使用注入验证器返回的UserPrincipal，匹配recipient才允许读取/答复；未绑定验证器503关闭，验证失败401。验证器不来自请求body，API拒绝伪造userId或triggerRef字段。生产者HTTP沿用现有本机API信任边界，ownerSessionId不是HTTP认证；MCP执行self/managed检查，不声称新建跨OS租户隔离。

Job兼容函数保留legacy jobId/taskId、independent executionOwner、runId/stepId和稳定interactionKey，仅生成请求并返回response_recorded等待事实。调用者须提供明确Job授权回调，不按createdBy或Job状态推导权限，不改变Job完成/继续/取消状态，不回退执行器。

所选choice的triggerRef来自原Card，不能由response替换。定向Trigger端口复检enabled、可选revision pin和权限，传expected_revision给运行时原子接纳。没有用通用Bus广播冒充只触发所选Trigger。响应持久化、Trigger持久接纳、执行完成是三个边界；缺adapter时unavailable；只有accepted+持久receiptId才记接纳；取消中断/异常后按原eventId查回执，缺失为unknown不盲投。明确rejected才可重试。无适配器实例不能清除别的实例in_flight/unknown领取。

Router仅增加显式user_inbox adapter扩展；默认未配置仍明确拒绝原始user_inbox投递。原QQ、Agent Inbox/Locker、QueueID和unknown逻辑不改。User Inbox存储根登记为不可自动清理。

契约：[设计/API及未冻结项](../../docs/design/User-Inbox-Interaction-Card-Contract.md)；[稳定Schema](../../packages/interactions/schema.json)。生产API schema：GET /api/interaction-cards/schema。用户API：/api/user-inbox；生产者API：/api/interaction-cards。

## 精确版本测试

最终 **258 passed，0 failed，0 errors，0 skipped**，JUnit time=25.189秒；其中本批新增Interaction专项 **21** 项。命令退出码0。

`python -m pytest tests/test_interaction_cards.py tests/test_interaction_card_api.py tests/test_interaction_card_http.py tests/test_agent_inbox.py tests/test_agent_inbox_http.py packages/qq/test_qq_api.py packages/qq/test_channels.py tests/test_data_catalog.py tests/test_session_queue_api.py tests/test_illegal_queue_wake.py tests/test_jobs_api.py tests/test_independent_job_bridge.py -q --junitxml=evidence/user-inbox-interaction-contract-20261023/final-verified.xml`

[命令与精确SHA](final-command.json) / [JUnit](final-verified.xml) / [完整日志](final-verified.log)。使用已有 D:/project/Pan-main/.venv/Scripts/python.exe，没有安装/修改共享依赖。

`compileall`、固定起点至源码SHA的 `git diff --check` 均退出0，见[静态结果](final-static.json)；Draft202012Validator.check_schema退出0，见[schema-check.json](schema-check.json)。

覆盖：可选输入组合及无隐式批准、一次性/相同答复重试/不同答复冲突、创建幂等、recipient与owner隔离、revision、并发双Store答复、response/Event同事务故障回滚、容量与严格字段、用户隔离分页、Job旧/新快照及授权、Trigger禁用/版本/权限复检、持久接纳后中断冷恢复、未知不重投、明确拒绝重试、并发dispatch原子领取、无适配器竞争保护、MCP越权/无用户答复能力，以及真实HTTP/MCP。

同时运行原Agent Inbox/Locker、QQ API/通道、Queue API、Illegal/Wake、数据目录、统一Jobs API和独立Job Bridge回归，未改Job/scheduler/QQ业务代码。

## 真实 HTTP / MCP 证据

使用自有随机端口 **56196**、临时profile、完整Pan lifespan。核对测试nonce、listener PID/父链、命令标记、cwd和所有使用到的数据根。服务PID **44844**。原QQ网关manifest为空，fake CLI执行文件固定为本树tests/support/fake_stream_cli.py。

- [完整实例与Card/Event/receipt身份](http-mcp/interaction-evidence.json)
- [服务日志](http-mcp/server.log)
- [监听/服务退出](http-mcp/cleanup.json)
- [fake进程均退出](http-mcp/process-cleanup.json)
- [自有临时证据来源](http-mcp/artifact-source.json)

stdio MCP真实发现并创建Card；MCP尝试respond被拒绝。无用户凭证HTTP响应401；合成用户验证后一次性持久answer/choice；生产TriggerReferenceAdapter通过受控合成Trigger端口接纳一次，重复response不重复提交，原eventId只有一条持久回执。Card/response没有进入Session history正文。服务正常退出、监听关闭，fake CLI PID47020不存在。

该端口的用户验证器与Trigger接收器均为明确的synthetic-test-only代码，不是生产登录、真实Trigger执行或Job继续成功的证据。未调用OAuth、真实provider、收费模型/账号、真实QQ/用户Worker；未访问8767/8768真实data。

## 失败、warning与未验证边界

前序失败与成功JUnit保存在preliminary/，不混入最终精确SHA计数。初版把response幂等键错误地全局约束到同一recipient，导致跨Card复用requestId的测试失败；已按card作用域修复。首轮HTTP断言误把创建的空history.jsonl当成context写入，实际文件0字节；改为严格验证无history正文，未skip或放宽“Card不进context”要求。最终修复了无adapter并发降级在途领取的问题并增加定点回归。

最终日志仍报告Pydantic forward-reference、Starlette/httpx/BlockingPortal弃用warning，以及 tests/test_independent_job_bridge.py 路径触发的 packages/scheduler/api.py:259 `coroutine broadcast was never awaited` RuntimeWarning。本批没有修改该Job/scheduler代码，也没有屏蔽warning；根因未在本任务定位，不能宣称该warning已修复。

明确未完成/未验证：

- 没有GUI、浏览器/OS交互验收、真实用户登录、真实通知送达。通知只保存元数据。
- 当前基线没有统一Trigger运行时/用户登录验证器；本批给出可用的注入端口，生产用户接口默认503关闭。实际runtime接线、持久接纳原子性和执行门禁由后续整合验证，不能把合成接收器冒充完成接线。
- Job兼容层不会自动让旧Job进入等待/继续/完成，也未连接真实独立Job服务。是否/如何将response驱动Job代次状态仍待确认。
- 未确认产品细节保持开放：用户主体命名、必填组合/多选多输入、过期/取消/撤回、已读/归档、卡片编辑、通知渠道、Trigger引用编辑策略。未增加这些默认政策。
- 没有Card与Job/Trigger之间跨文件事务。冷恢复验证为确定性取消/异常注入及重新打开存储，不冒充在特定CPU指令点OS硬杀。
- 未运行全仓全量测试或前端build；本批前端产品代码无改动。没有部署、push、release或真实服务重启。
