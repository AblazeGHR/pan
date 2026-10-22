# User Inbox / Interaction Card 与 Job / Trigger 后端交界

本批基于 7446efcb55545be35e76b4845941991202b8cd93，在独立 feature/user-inbox-interaction-contract-20261023 工作树实现；原 QQ / Agent Inbox / Locker 与 Session Queue 的消费语义保持原契约。

## 已授权与当前基线

用户授权后端统一模型、兼容接入、schema/API/定向测试；可选 answer 输入、可选 choice trigger、一次性 response、通知元数据、幂等与权限。不实现前端GUI，不扩展OAuth或真实provider，不整合其他TA的Job执行器。

基线已有 packages/jobs/api.py 的统一 Jobs HTTP API，区分 legacy registry 与 opt-in independent service，不应在服务不可用时回退执行。该SHA没有 packages/automation 运行时，也没有User Inbox用户登录验证器。只读核对了已提交候选 ffb048b0747dd545060420f275e7a49f510cc20d 的 Event契约（id/type/schemaVersion/source/occurredAt/payload）与 authorized(header) 登录边界；没有合并该候选或任何未提交内容。其Event/Trigger/Action、Job执行状态不能用本批card状态替代。

## 最小统一模型

Interaction Card 是 User Inbox 中的持久请求，不是 Locker，不属于Session Queue。显式 recipientId 为用户身份验证器返回的稳定主体ID，身份命名/账号/登录UI由接入方决定，不在此发明用户体系。ownerSessionId 表明请求归属，创建者/关联ID不是执行授权。

内容包括 title/body、可选 answerInput、choices[]（每项id/label，可选 triggerRef）、notification 展示元数据、可选 correlation。answerInput缺省时不接受answer；choice只能提交已有ID，响应不能提供trigger、Job、URL或shell。answer和choice可同时携带；没有控件的卡片只读，不能隐式变成“批准”。本兼容层不增加必选项/自动选默认值/答复优先级/超时默认同意等未确认政策。

Card请求不可原位编辑。创建由owner+requestKey幂等，内容/recipient变更明确冲突；response在单SQLite事务中持久，状态从open到responded至多一次。相同用户、相同requestId与相同answer/choice重试返回既有response；不同正文或第二份response冲突；expectedRevision防止过期响应。读取不答复、不消费，不写入任何Session context。

notification只是展示意图（如title/summary），不发送OS通知、不发送正文到Session、不代表送达，也不含可执行deeplink。自动通知渠道、已读/归档、过期/取消、卡片编辑、多个answer字段、选择多选、附件输入、审批政策和响应后的具体Job变更均保留开放，不在本批冻结。

## Job与Trigger交界

Job兼容适配接收已授权调用者提供的Job快照，保留canonical jobId、旧scheduled taskId、executionOwner、runId等关联。只是生成Card请求并投影response，不把Card答复等同于Job完成/继续/重试/取消；不改Job registry/scheduler/执行器。外部调用者仍须核对Job归属和代次，关联字段本身不提供权限。

持久response生成稳定的 pan.interaction.responded Event包络。只有选中的choice带triggerRef时才创建待投递意图；triggerRef仅是id及可选revision引用，不是Action快照、URL或命令。真正接入需要显式注册内部Dispatch adapter：授权/启用/版本/目标/Pause等由受信adapter复检；accept返回持久receiptId只证明接纳，不能声称执行完成。无adapter时保持unavailable；明确无副作用rejected可重试；中断/异常/无法核对的结果为unknown，不盲重投。该契约可接未来Trigger运行时，不伪装当前SHA已经执行Trigger。

## 权限与兼容接入

用户list/read/respond/dispatch接口都必须由注入的受信用户验证器返回principal；默认未配置时503关闭。仅匹配recipientId可读/答复，身份不来自请求body、ownerSessionId、createdBy或MCP。验证器可包装既有 authorized(header) 接口；不新增密钥/登录/OAuth实现，不持久化Bearer。Agent MCP只提供请求create/get，按原self/managed权限限定ownerSessionId，不提供用户response入口。

Router的user_inbox仍为显式opt-in adapter：未经配置继续明确拒绝；接入时必须传完整Interaction Card及受信生产者上下文，不把任意QQ文本自动升级为用户审批卡片。原Agent Inbox/Locker不改消费模式、原QueueID或unknown语义。

## API v1 与稳定schema

完整JSON Schema位于 [packages/interactions/schema.json](../../packages/interactions/schema.json)，可由 `GET /api/interaction-cards/schema` 获取。主要定义为 Create / Definition / Respond / Card / Response / Delivery / Event / JobRef / TriggerRef。Schema严格拒绝未知可执行字段；64KiB payload、32 choices、32K answer字符与默认1000张未答复card为本实现的技术上限，不代表用户已经确认最终产品容量/保留政策。满容量明确拒绝，不截断未答复内容。

| API | 权限/输入 | 返回与边界 |
| --- | --- | --- |
| POST `/api/interaction-cards` | 本机生产者入口 `{ownerSessionId,recipientId,requestKey,card}`，owner须存在 | `{card}`；无执行或自动批准能力 |
| GET `/api/interaction-cards/{id}?ownerSessionId=...` | 本机生产者归属查询 | `{card}`，可供原请求Job等待response；不改变Job状态 |
| GET `/api/user-inbox?limit=100&after=...` | 每次验证用户，limit1..500，after为该用户已可见card ID | `{schemaVersion:1,cards,nextAfter}`；稳定插入顺序分页，不返回其他用户数量/ID |
| GET `/api/user-inbox/{id}` | recipient用户 | `{card}`；读取不答复 |
| POST `/api/user-inbox/{id}/respond` | recipient用户，`{requestId,expectedRevision,answer?,choiceId?}` | `{card,dispatchError?}`；先持久response，再尝试选定trigger接纳 |
| POST `/api/user-inbox/{id}/dispatch` | recipient用户 | 对已记录response继续接纳/核对；不接受新的choice、target或正文 |
| MCP `interaction_card` | operation=create/get，owner_session_id缺省caller；原self/managed权限 | 只导出生产者操作，没有用户respond/dispatch能力 |

创建幂等键作用域是ownerSessionId+requestKey；response幂等键作用域是card+recipient+requestId。重复创建或答复可能返回已经推进的最新状态，但绝不重建原内容或产生第二份response。Card.revision目前只在首次答复时0→1；不把投递状态变化伪作第二次用户答复。

返回Card包含 definition（原请求）、response（null或唯一记录）、delivery与status(open/responded)。响应Event拥有稳定eventId、首次occurredAt、core/pan来源和payload中的cardId/ownerSessionId/recipientId/response/correlation/可选triggerRef。未选trigger时delivery=not_requested，Event仍可由受权生产者读取作为原Job的等待事实，不自动广播触发其他Trigger。

用户验证未配置=503，验证失败=401；recipient不匹配=404避免泄露；拒绝Trigger授权=403（若response已先持久，respond返回已记录Card并附dispatchError，不能宣称未答复）；数据无效=422，revision/幂等/容量冲突=409。关联ID或客户端自报createdBy不能替代授权。

生产者HTTP沿用现有本机API信任边界，ownerSessionId不是HTTP认证凭据；MCP执行self/managed约束。本批没有声称生产者HTTP具备跨OS用户/租户隔离。用户独占respond/dispatch始终走注入的验证器，不能由本机生产者字段绕过。

## 接入方式与未冻结项

User auth由 `app.state.user_inbox_principal(request)` 返回 `UserPrincipal(id)`（可async）；缺省关闭。接入已有UserAuth时，仅在其 `authorized(Authorization)` 成立后，由受信代码映射固定用户主体；不能从body读取userId。本批不复制另一分支登录实现、不生成新本机密钥、不注册登录路由、不持久化Bearer，也不改变原有登录策略。

Trigger端口由 `app.state.interaction_trigger_adapter` 显式注入，必须具备 authorize(event) / accept(event) / lookup(eventId)。本批提供 TriggerReferenceAdapter 对接 get_trigger / policy / directed submit / receipt lookup：复检enabled、可选pin revision和权限；submit必须原子复检所传expected_revision与目标执行门禁。不得直接用通用Bus广播代替定向接纳，否则可能触发非选中的其他Trigger。accepted必须带持久receiptId；内存Bus受理或仅返回queued不能当作持久接纳。接纳不是执行成功。

Job兼容层 `job_reference / create_job_card / job_response` 处理legacy jobId/taskId和independent-job-service快照；create_job_card要求调用者提供明确Job授权回调。接口保留runId/stepId/interactionKey的幂等关联；不按Job状态猜测允许答复后做什么，不回退本地Job执行，不连接真实独立Job服务。

待用户或后续运行时接线确认：登录主体命名、答复是否驱动Job等待/继续及其代次协议、Trigger引用的产品编辑策略、过期/取消/撤回、归档已读、必填answer/choice组合、多选与多输入、通知送达渠道和展示、卡片编辑/版本迁移。这些不是本批成功投递或已完成GUI的声明。

数据位于本profile `user_inbox/cards.sqlite`，SQLite FULL synchronous，response/Event/意图在同一事务。与Job/Trigger存储不承诺跨文件事务；Trigger接纳前持久in_flight，冷恢复只查原eventId回执；missing/过期回执保留unknown，不自动重投。明确rejected才可重试；accepted单调不回退。读取/答复不会写Session history/Queue。
