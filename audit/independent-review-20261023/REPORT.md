# 统一 Job / Inbox / Locker 组合候选独立验收

## 结论

**原候选：需修复，暂不通过。** 已复现 Interaction Card 回应被明确拒绝后表单仍锁死；另确认组合后的生产 Locker 仍缺少用户身份接线。前者提供最小隔离修复，后者保留 fail closed，不能用合成浏览器通过替代真实用户入口可用性。

- 被验收树：`D:/project/pan-worktrees/job-inbox-panel-composition-20261023`
- 被验收分支：`feature/job-inbox-panel-composition-20261023`
- 固定被验收 SHA：**`1ca7cbd3d19635064fd50cfbcde3a633a260c83a`**；开始/结束均 clean，未移动 HEAD。
- 修复/报告树：`D:/project/pan-worktrees/job-inbox-independent-review-20261023`
- 修复分支：`review/job-inbox-independent-20261023`
- 精确修复源码 SHA：**`26ea1cb266569ea5bf197983cc3d5f56371953eb`**；后续仅提交本审阅证据目录。
- 修复候选只解决明确校验拒绝后的输入修正，以及纠正组合契约中“没有 UserAuth”的过时描述；不擅自定义 recipient 身份或定向 Trigger 产品语义。它不是完整生产接线通过的声明。

本轮开始前核对 root/branch/HEAD/status。首次清单命令误在被验收树创建了自有 `evidence_names.txt`，已立即按精确路径移除；产品、测试、配置、Git ref 和已有证据均未修改。之后所有运行输出、测试、新修复均在独立树。未操作 main/practical、真实服务/账号、8767/8768、OAuth/private exporter，也未派子任务或安装共享依赖。

## 发现与分级

### F1 — P1：明确拒绝后无法修正 answer/choice（已在隔离候选修复）

原 `LockerView.tsx` 在请求前将完整 payload 记入 `intent.current`，任何异常都保留该意图，textarea/select 又以意图非空为条件禁用。结果即使服务端明确拒绝且未持久回应，用户仍不能修正输入，只能重复相同被拒绝内容。

真实可触发案例：answerInput.maxLength=32000，输入 24000 个中文字符，低于字符上限，但 Respond 的 JSON 超过 64 KiB。实际 API 返回 **409/code=capacity**，再次 GET 证明 `response=null`；原表单保持锁定。补充 422 校验拒绝也复现同样问题。新增断言先失败，未放宽原断言或 skip。

修复：`lockerModel.ts` 保留结构化 HTTP status/code，`LockerView.tsx` 仅在 **422 或 409/capacity** 后清空被拒绝意图，允许修正并建立新意图；普通 409 冲突、网络失败、未知结果继续保留原 requestId/完整 payload，禁止改选重放。原 uncertain 重试回归继续通过。修复没有改变服务端一次性回应、Trigger 回执或 Queue 语义。

### F2 — P1 组合接线缺口：独立 Locker 的生产用户入口不可用（未擅自修复）

组合树有 `packages/automation/auth.py::UserAuth` 和 Jobs 的用户授权 UI；但生产 `packages/interactions/api.py:24` 仍只读取 `app.state.user_inbox_principal`，全树只有测试 launcher 绑定该属性。`lockerRequest` 不带现有 Automation Bearer，页面没有登录控件或现有主体映射。

独立测试使用临时目录中的真实 UserAuth 生成并验证会话：`auth.authorized(Bearer)=true`，但在未启动 lifespan、未读取真实 profile 的生产 ASGI app 上携此 Bearer 访问 `/api/user-lockers` 仍是 **503/user_auth_unavailable**。这证明生产默认 wiring 缺失；不把 callback 的 fail closed 本身当成权限漏洞。已有浏览器证据绑定 synthetic UserPrincipal，不能证明该入口已接现有登录。

本轮已向用户询问是否固定单用户 recipientId=`user` 复用现有认证，或明确接受本批只交付扩展点；交付时未收到主体映射确认。因此保留关闭状态，没有从请求 body/Session 身份猜用户、开放所有已有卡片或新增认证机制。若 MA 按“可实际使用的用户 GUI”验收，此项阻断；若明确缩小为带受信适配器的 GUI/API 契约，必须在验收范围中写明，不可默认为完整可用。

### F3 — 预留运行时边界：choice Trigger 只验证合成适配器

`interaction_trigger_adapter` 的生产绑定亦不存在；选择 trigger 后 response 能持久，但 delivery=unavailable。已有 TriggerReferenceAdapter 为定向接纳接口，没有偷用通用 Event Bus，符合禁止误触其他 Trigger 的边界。按原“准备契约、未确认细节不先决定”范围，这是保留项；不能宣称组合后真实 Trigger/Job 已被用户答复驱动。没有为了通过验收调用真实运行时/provider。

## 已确认设计逐项核对

| 核对项 | 代码/证据结果 | 判定 |
| --- | --- | --- |
| User Inbox 与 Chat 独立，页面称 Locker | 独立 `/react/locker`、共享导航入口，右侧 Locker Message；不塞入 Chat 或写 Session history | 符合结构契约；生产身份另见 F2 |
| History 只指 Session Chat | Locker 只复用 MarkdownRenderer/MessageTimestamp/附件资源；不把独立存储称 Session 历史；附件绑定来源 Session | 符合本批边界 |
| 真实 Locker 来源隔离 | `(recipientId,ownerSessionId)`，来源目录保留，来源不可用不删除内容；普通 Chat/QQ/Job 通知不建用户 Locker | 符合当前 Session 来源契约；未来插件域未扩展 |
| 聚合 Locker 纯前端 | lockerProjection 按 Workspace 过滤目录，将原 Card 按 createdAt/id 排序；无聚合存储或游标 | 符合 |
| 用户只有 read/unread | 原 Card 的 is_read，原 ID PUT；分页 after 为无持久状态查询参数，未新增消费者进度；IntersectionObserver 标原消息 | 符合 |
| Agent Inbox 无独立 GUI | 无新增 Agent Inbox 路由；HTTP/MCP 共享访问与 msgbridge 管理边界保留 | 符合 |
| reject 与 delete | reject 原子写唯一回应和内部 outbox，保留 Card；delete 仅终态，用户侧清理、不通知，留 tombstone/必要内部回执 | 符合；并发/冷恢复回归通过 |
| summaryBody 截断 200 字符 | reject 取 `summaryBody[:200]`，通知无完整 body/answer/附件；不是截断完整卡片正文 | 符合；多字节摘要用例通过 |
| Agent Inbox full/notification | full 全目标持久接纳才共享消费；notification 仅身份提醒不消费；原消息/Queue ID 和 unknown 核对保留 | 符合；相关原实现对比无整合修改 |
| Job/Template 统一 | Action 引用、独立 Template enabled/config.enabled、修订/幂等意图、实际 Attempt snapshot/清理及用户权限 gate | 定向代码与测试未发现新阻断 |
| 旧 Job 投影 | project 不写原记录，config.enabled=false；含诊断/unknownFields；绝不猜新日期或将不明历史变即刻执行 | 符合已冻结迁移边界 |
| 通知/Queue 边界 | Job 仅 IDs/status、稳定 per-Attempt eventId，走原 Session Queue；不入 Locker、不消费 Locker；Queue 落盘不等 CLI | 符合；readonly/Illegal/Wake 定向通过 |

整合父输入 `4e51ec704bd3d2d04c6913da7c8f90f76fbc93f7` 与 `ba847160a01daf0049bb84575c427317ab7db236` 都是候选祖先；`8418d642` 为二者 merge。对比 Locker 输入后 Agent Inbox、QQ、Inbox Queue 及 Card 产品实现没有额外整合改写；对比最终测试兼容提交 cfe2663f 后只有证据提交。相对很早起点 140455 的大 diff 含既有 ETA/provider 历史，不把它全部当作本次 merge 新增，更不借此扩大 OAuth/private exporter 范围。

## 证据复核与本轮实测

原组合证据 JUnit 逐项解析：**517 Python、121 Vitest、2 HTTP/browser**，均 0 failures/errors/skipped，与其报告吻合；这是原输入证据，不冒充本轮独立运行。[解析与祖先核对](input-evidence-check.json)。

本轮独立测试结果（分批重叠，不累加宣传成全量覆盖）：

| 批次 | 通过/失败/错误/skip | 退出码 | JUnit |
| --- | --- | --- | --- |
| 原产品源码 + audit observation + 迁移/Job/Template/Agent Inbox/Card 定向 | 116/0/0/0 | 0 | baseline-python.xml.gz |
| 新增 422 修正断言，原页面 | 7/1/0/0 | **1** | baseline-ui.xml.gz |
| 仅修 422 时补容量拒绝断言 | 8/1/0/0 | **1** | capacity-red.xml.gz |
| 422 修补 + Markdown 原上下文回归 | 43/0/0/0 | 0 | fixed-ui.xml.gz |
| Job system/completion/recovery/overlap、Queue/Illegal/QQ/API | 152/0/0/0 | 0 | runtime-boundaries.xml.gz |
| 生产未绑定身份与实际 Unicode 容量拒绝观测 | 2/0/0/0 | 0 | auth-capacity-observations.xml.gz |
| 最终 Job 表单/Template/Locker/快捷键 | 24/0/0/0 | 0 | final-ui.xml.gz |
| 最终生产构建后的独立 HTTP/MCP/Chromium | 1/0/0/0 | 0 | browser.xml.gz |
| 最终 npm run build | tsc/Vite/88 项压缩资源校验通过 | 0 | build-final.log.gz / build-final-result.json |

116 项中包含 4 项迁移演练：临时旧 Job JSON 的 unknown/enabled/schedule 投影、临时 Action registry/Template 未知字段、Queue public projection 不泄露 runtime snapshot、QQ JSON 的临时物理副本导入/源字节哈希不变。另有真实旧 v1 SQLite Card 的幂等迁移用例。没有打开或迁移真实 profile，也未把合成副本演练说成生产迁移完成。

实际浏览器使用 **127.0.0.1:64003**、自有临时 profile、空 QQ manifest、fakeCLI、synthetic principal/Trigger；先核 nonce、PID/父链、cwd、listener、全部 data 根，再测试。MCP 创建、提交 answer/choice、只读刷新、拒收保留、删除与 receipt、原消息标读均通过，页面错误 0，拒收通知恰一条 durable accepted；退出后 listener 关闭。它验证产品页面及已接的合成端口，不验证 F2/F3 生产绑定。[浏览器结果](browser.json)、[实例](interaction-evidence.json)、[清理](cleanup.json)、[桌面](locker-desktop.png)、[移动 viewport](locker-mobile.png)。

## 可复核命令

均在隔离修复树运行；Python 使用既有环境并清除继承 PAN_*，没有安装共享依赖。前端 node_modules 是既有依赖的目录联接，仅本树构建输出。

```powershell
# cwd: D:/project/pan-worktrees/job-inbox-independent-review-20261023
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_independent_composition_observations.py tests/test_job_inbox_migration_drill.py tests/test_job_inbox_boundaries.py tests/test_job_unified_contract.py tests/test_job_action_templates.py tests/test_agent_inbox.py tests/test_interaction_cards.py tests/test_user_locker_lifecycle.py -o addopts= -q --junitxml=audit/independent-review-20261023/baseline-python.xml
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_job_system_actions.py tests/test_job_completion_recovery.py tests/test_job_overlap.py tests/test_interaction_card_api.py tests/test_session_queue_api.py tests/test_illegal_queue_wake.py packages/qq/test_qq_api.py packages/qq/test_channels.py -o addopts= -q --junitxml=audit/independent-review-20261023/runtime-boundaries.xml
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_independent_composition_observations.py -o addopts= -q --junitxml=audit/independent-review-20261023/auth-capacity-observations.xml
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_user_locker_browser.py -o addopts= -q --basetemp audit/independent-review-20261023/browser-runtime --junitxml=audit/independent-review-20261023/browser.xml
# cwd: .../packages/web
node node_modules/vitest/vitest.mjs run src/components/jobs/UnifiedJobForm.test.tsx src/components/jobs/JobTemplateEditor.test.tsx src/views/LockerView.test.tsx src/shortcuts/registry.test.ts --reporter=default --reporter=junit --outputFile.junit=D:/project/pan-worktrees/job-inbox-independent-review-20261023/audit/independent-review-20261023/final-ui.xml
npm run build
```

baseline-python 的 116 项运行时 audit observation 只有 auth 用例；随后增加 Unicode 容量观测，用独立 2 项批次运行。当前直接重跑第一条将是 117 项，不将历史 116 冒充当前全部计数。fixed-ui 运行命令为 `vitest run src/views/LockerView.test.tsx src/components/chat/MarkdownRenderer.test.tsx`，在增加 capacity 参数化前执行；最终 24 项覆盖两种拒绝和原 unknown 语义。

[总览 JSON](verification-summary.json) 给出每批 counts/exitCode、源码 SHA 与原树结束 status/HEAD。所有原始 XML/日志按原字节 gzip 保留，避免工具输出尾随空格引起 diff-check 噪声；解压后与本地原文件逐字节核对一致，不编辑或删减失败证据。

## 剩余门禁与未验证

- F1 最小修复已通过定向自动化与生产构建，交 MA 复核；不直接替换或合并组合树。
- F2 主体/recipient 映射与现有认证桥接须明确，未完成时不能把默认 Locker panel 报作生产可用。F3 真实定向 Trigger/Job 等待继续属于后续运行时接线，不用通用广播替代。
- 未运行完整仓库测试、真实账号/provider/QQ、真实手机/OS 交互、真实附件文件打开、生产迁移或海量聚合性能；没有任何部署事实。
- Pydantic/Starlette/httpx 警告、前端路由/大 chunk 提示保留在日志，不新增 skip/xfail、弱化断言或扩展修复到无关业务。
- 用户已确认的 Job/Agent Inbox/Locker 内容与 Queue 边界在本轮定向范围内未发现新的冲突；不等同所有未来插件、身份域和 provider 均被验收。
