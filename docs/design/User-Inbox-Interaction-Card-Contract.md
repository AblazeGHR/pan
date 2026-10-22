# User Inbox / Interaction Card 与 Job / Trigger 后端交界

本批基于 7446efcb55545be35e76b4845941991202b8cd93，在独立 feature/user-inbox-interaction-contract-20261023 工作树实现；原 QQ / Agent Inbox / Locker 与 Session Queue 的消费语义保持原契约。

## 已授权与当前基线

用户授权后端统一模型、兼容接入、schema/API/定向测试；可选 answer 输入、可选 choice trigger、一次性 response、通知元数据、幂等与权限。本次追加授权实现独立 User Inbox GUI，页面统一命名 Locker，不并入 Chat；Agent Inbox/Locker 及 msgbridge 不新增 GUI。不扩展 OAuth 或真实 provider，不整合其他 TA 的 Job 执行器。

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

待用户或后续运行时接线确认：登录主体命名、答复是否驱动Job等待/继续及其代次协议、Trigger引用的产品编辑策略、过期/取消/撤回、必填answer/choice组合、多选与多输入、通知送达渠道和展示、卡片编辑/版本迁移。这些不是本批成功投递或已完成GUI的声明。

数据位于本profile `user_inbox/cards.sqlite`，SQLite FULL synchronous，response/Event/意图在同一事务。与Job/Trigger存储不承诺跨文件事务；Trigger接纳前持久in_flight，冷恢复只查原eventId回执；missing/过期回执保留unknown，不自动重投。明确rejected才可重试；accepted单调不回退。读取/答复不会写Session history/Queue。


## 2026-10-23 已确认增量：用户 Locker 与独立 Panel

本节覆盖此前未冻结的 read/unread、拒收、删除与 GUI 口径。History 仅指 Session Chat 历史。`/react/locker` 是独立路由，通过共享导航入口打开，页面只使用 Locker / Locker Message。Agent Inbox 的共享消费、QQ history、msgbridge 订阅和原 Queue 语义不变。

真实用户 Locker 的稳定来源域为 `(recipientId, ownerSessionId)`：本阶段专用 Interaction Card 生产者是来源 Session/Locker Agent。只有显式 `POST /api/interaction-cards`、MCP `interaction_card(create)` 或显式 Router adapter 能建立此容器。普通 Chat、QQ、插件或 Job 通知不自动建用户 Locker。不同来源 Session 与用户隔离，首条专用投递建立真实 Locker；末条记录删除后仍保留来源容器。未来插件来源扩展需显式版本化域，不按显示名称合并来源。

用户是唯一消费者。每张原始消息只有 `read:boolean`；没有消费位置、消费者私有进度或 Agent Inbox 的 shared consumption。列表/分页查询本身不标读，专用 `PUT .../read` 显式设置，允许回到 unread。Panel 用 IntersectionObserver 观察进入右侧视口的原卡片，写回原 id 的 read 状态；加载到内存但未展示的卡片不标读。`after` 只是无持久状态的查询分页参数，不是消费游标。

聚合 Locker 完全是前端投影：读取真实 Locker 的来源目录（名称、Session ID、当前有效 Workspace IDs），按所选 Workspace 过滤，合并并按 `(createdAt,id)` 排序。后端不存聚合容器、聚合读状态或额外游标。来源 Session 缺失时标为 unavailable，消息保留。Workspace 使用既有 effective membership，不能凭客户提交的 Workspace ID 扩大 recipient 权限。

### Card 定义、回应与通知

`definition.body` 为完整固定正文，`summaryBody` 为固定通知摘要正文；发送通知时最多截取前 200 个 Unicode 字符，不携带完整 body/附件/answer。旧 v1 创建请求未带 summaryBody 时，视图确定性取旧 notification.summary 或 body，既有创建 signature 不改；旧 v1 数据只在本 profile SQLite 内做增量表/列迁移，不读写真实 profile。附件使用既有 opaque attachment ID 与安全下载/Markdown 渲染资源，Panel 显式绑定来源 Session，避免借用当前 Chat 的文件上下文。附件是否可用仍由既有来源 Session 附件接口核验，不扩大文件权限。

answerInput 与 choice/trigger 均可选。`submit` 允许没有控件的卡片作一次性明确提交；旧 `respond` 继续拒绝无输入的空回应，以保留旧调用契约。choice 只传原定义 choiceId，不接受临时 trigger/URL/shell。提交保留兼容 `status=responded`（Panel 显示已提交），拒收为 `status=rejected`；二者在同一 SQLite 原子领取一次 response，之后定义只读且不可改写。相同 requestId+相同结果幂等；改结果、另一次 submit/reject 或 stale revision 明确冲突。拒收结果具有 `response.outcome=reject`，不能当作 Job 成功或自动继续执行。

reject 在同一事务建立唯一内部 rejection outbox，定向来源 Locker Agent 的现有 Session Queue。正文是 `Interaction rejected: ` + `summaryBody[:200]`，结构化 envelope 带 `type=interaction.rejected/cardId/lockerId/summaryBody`。稳定 notice ID 与原 QueueID、逐目标持久回执用于冷恢复；Queue 落盘即接纳，不等 CLI，不承诺跨文件事务。claim 后中断只查原 QueueID，缺回执保持 unknown，不盲目重投。明确拒绝保留 pending，启动恢复或后续 reject 投递轮次可重试。Pause/Illegal/lease/owner/generation 仍由原 Queue/Worker gate 控制。

用户只有完成后才能 delete；delete 不创建 notification。删除原用户记录，留下 cardId/owner/recipient/创建签名哈希的最小 tombstone，禁止同创建键复活。pending/unknown 通知或 Trigger 仍是独立内部投递记录，不是第二份用户容器；接纳后清除其待投正文/Event。来源生产者的 response/Event 回执独立保留，可经 receipt 入口读取，用户删除不会抹掉已回应事实。这里只保证逻辑删除，SQLite 自由页或外部备份的物理擦除不在本批。

### 增量 API / MCP

| 入口 | 输入与稳定结果 |
| --- | --- |
| GET `/api/user-lockers` | recipient 验证；`{lockers:[{id,sessionId,sourceName,workspaceIds,available,total,unread}]}`；只列真实容器 |
| PUT `/api/user-inbox/{id}/read` | recipient；严格 `{read:boolean}`；`{card}`；不改变 response/revision |
| POST `/api/user-inbox/{id}/submit` | recipient；同 Respond schema，空控件可显式确认；`{card,dispatchError?}` |
| POST `/api/user-inbox/{id}/reject` | recipient；严格 `{requestId,expectedRevision}`；`{card}`；通知与卡片保留分开 |
| DELETE `/api/user-inbox/{id}` | recipient；仅终态；`{deleted:true,id}`，重复同用户删除幂等；不通知 |
| GET `/api/interaction-cards/{id}/receipt?ownerSessionId=...` | 既有生产者归属；`{receipt:{cardId,ownerSessionId,recipientId,response,event}}`；用户删除后仍保留 |
| MCP `interaction_card(operation=receipt)` | 与 create/get 相同 self/managed 权限；没有用户 submit/reject/delete/read 入口 |

公开 JSON Schema 增补 Locker / CardPage / Read / Reject / ProducerReceipt；Card 增补 lockerId/read/sourceName，保留 schemaVersion=1 和旧 respond/Event/JobRef 兼容。sourceName 的目录值来自当前来源 Session；Card 自身 sourceName 为稳定来源 ID fallback，UI 用目录名称展示。

### UI 和运行时边界

左栏真实 Locker 列表，Workspace 筛选及聚合 Locker 按钮；右栏按时间展示 Locker Message，显示来源名与 Session ID。复用 MarkdownRenderer、MessageTimestamp、安全附件链接和 API 分页。服务端每页 100 条，当前前端完整读取可见用户分页后合并，分 20 条展示；聚合不是懒加载跨来源归并，本批不声称大规模无限历史性能。页面恢复焦点/刷新重新读取，AbortController 与代次保护阻止过期请求覆盖切换；回应后的只读输入取服务端 response，网络结果不明的重试复用原 requestId 和完整 payload。

本基线没有可复用的生产 UserAuth 实现。继续由 `app.state.user_inbox_principal` 注入受信主体，缺失则页面/API 明确 503；不能用 Session/MCP 身份或自行新增本机凭据开放用户回应。本批真实浏览器闭环使用自有随机端口、临时 profile、synthetic 用户验证器、synthetic Trigger 与 fakeCLI；不代表生产登录或真实 Trigger/provider 已接线。生产身份适配器、真实 Job 等待/继续协议、真实 provider、OS 附件交互及人工验收仍分别验证。
