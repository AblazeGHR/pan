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

`definition.body` 为完整固定正文，`summaryBody` 为固定通知摘要正文；发送通知时最多截取前 200 个 Unicode 字符，不携带完整 body/附件，用户 answer/choice 随回应通知发送。旧 v1 创建请求未带 summaryBody 时，视图确定性取旧 notification.summary 或 body，既有创建 signature 不改；旧 v1 数据只在本 profile SQLite 内做增量表/列迁移，不读写真实 profile。附件使用既有 opaque attachment ID 与安全下载/Markdown 渲染资源，Panel 显式绑定来源 Session，避免借用当前 Chat 的文件上下文。附件是否可用仍由既有来源 Session 附件接口核验，不扩大文件权限。

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

原 User Inbox 分支缺少 UserAuth；当前组合基线已包含 Automation UserAuth，但未将其绑定到 Locker 身份验证，也未由 Locker 请求附带现有 Bearer。当前继续由 `app.state.user_inbox_principal` 注入受信主体，缺失则页面/API 明确 503；不能用 Session/MCP 身份或自行新增本机凭据开放用户回应。本批真实浏览器闭环使用自有随机端口、临时 profile、synthetic 用户验证器、synthetic Trigger 与 fakeCLI；不代表生产登录或真实 Trigger/provider 已接线。生产身份适配器、真实 Job 等待/继续协议、真实 provider、OS 附件交互及人工验收仍分别验证。


### 独立验收修补（2026-10-23）

Locker 表单区分明确的回应前拒绝与不确定结果：HTTP 422 和 `409/detail.code=capacity` 均发生在 response 持久化前，允许修正输入并建立新意图；普通 409 冲突、网络异常与服务端结果不明继续保留原 requestId/完整 payload，不自动改选或盲目重投。声明的字符上限不取代 64 KiB JSON 上限，多字节 answer 可能在字符上限内被容量拒绝。

生产身份主体到 recipientId 的映射和定向 Trigger 的实际运行时接线仍缺失；不能将合成验证器/Trigger 浏览器证据解释为这两项已完成。复用既有认证不涉及新增 OAuth/private exporter，但不能在未确认 recipient 命名时擅自开放全部已有卡片。

### 生产宿主接线修补（F2/F3，2026-10-23）

本节替代上文“生产身份未接线/缺省仅注入验证器”的历史边界。Pan 是单用户 profile：本 profile 的既有 Automation UserAuth 验证成功后，稳定映射为 `UserPrincipal('user')`；这是 profile 内的用户收件人键，不是 OS 用户、Session ID 或多租户认证。复用 `POST /api/automation/login`、`automation/user.key` 和既有最长 12 小时内存 Bearer；logout/过期/进程重启撤销凭据，新的登录仍对应同一 recipient。Locker 使用共享内存凭据和登录控件，所有用户读写均携带 Authorization。请求 body、URL、自报 recipient/userId 或生产者 owner 不能改变用户身份。没有运行中的认证宿主仍关闭；错误/缺失凭据 401，别人的卡片 404。

兼容决策：旧 `recipientId='user'` 卡片无需改写即可访问；其他旧 recipient 原值、Card ID、signature、response、Event 和 tombstone 保留，不自动合并或改名，也不因本地登录而暴露。无法证明其归属的旧别名仍不可访问，需要后续明确授权的迁移工具；本批不猜测映射。SQLite 仅新增 `response_outbox` 表，不回写旧卡片。临时副本演练对比源文件 digest 和旧行逐字段内容。受信宿主注入 principal/Trigger adapter 的扩展口保留，但产品验收使用真实 UserAuth 和运行时，非合成端口。

生产 lifespan 在既有 Automation Service 启动后，以受信 `core/pan` handle 注册 `pan.interaction.responded` schema。选中 choice 经 TriggerReferenceAdapter 检查启用、可选 revision pin、事件类型、recipient 和依赖资格；提交阶段在已有 Trigger writer lock 内再次核对 revision/enabled/依赖，锁内落盘只在 I/O executor 运行，不跨 Action await。Event Store 保存 **唯一选定 Trigger** 的目标快照，不经通用广播选择其他 Trigger/Job；提交 receipt 是 durable Event ID，不是 Action 完成。现有 dispatcher 建立 Run，以 Event+Trigger 去重，沿用 Action/Group pin/Pause/Illegal/业务门禁和取消/恢复。Event Store 与 Card SQLite 不宣称跨文件原子；in_flight/unknown 冷恢复只查原 Event ID，缺回执保留 unknown，不自动重新执行。disabled/stale 卡片保留显式错误，不阻止服务启动。

提交 response 同事务写唯一 response outbox。CLI 文本为 `Interaction submitted: ` 或 `Interaction rejected: ` 加 JSON，包含 cardId、responseKind（submit/reject）、summaryBody[:200]，以及用户实际提交的可选 answer/choiceId；这些字段同时保留在 envelope。只通知用户回应已记录，不暗示 Trigger/Job 执行成功；不转发完整 body/附件。稳定 QueueID、落盘 receipt、unknown 只查原回执；接纳后删除 outbox payload。reject 不执行 choice；delete 不生成新通知。旧已接纳/unknown 通知不通过新 QueueID 补发字段，以免重复。Agent Inbox 的共享消费与用户 read/unread 不变，没有新增 Job wait/resume 协议。

## 2026-10-23 MA 决策：卡片执行可信绑定

本节覆盖早期“生产者 label + 可选 Trigger revision 足够执行”的边界。`Create` 可含 producerSessionId（MCP 由可信 caller 注入；本地内部 HTTP 沿用既有受信调用边界），服务端按该来源和 owner 的既有 self/managed 范围核验全部 Session 目标，绝不把用户 Bearer submit 当作来源权限。没有身份时不得猜测来源授权。无 triggerRef 的普通卡片不依赖 Automation、不产生绑定；普通插件消息不自动进入 Locker。

创建时在既有 Trigger/Action writer 下解析固定 Trigger revision/digest、Action ID/digest、参数映射、selector 和来源授权依据，在同一写入范围内存入 Card 的独立 bindings 列；幂等 create 返回原绑定，不刷新到新定义。生产者不能提交 `bindings`/`trustedChoices`/Action 对象。Card 的 `trustedChoices[choiceId]` 是服务端输出，含 operation 摘要、固定 targetIds、arguments、Action digest、Trigger revision 与授权依据；前端必须展示该可信内容，不能只展示生产者 label。

当前可证明并接线的是 Session Operation 的 explicit selector。event/query selector 不能证明创建时目标范围；其他执行能力尚未提供完整效果/来源范围证明，返回409 incomplete，不创建卡片。不新增全局目标白名单、不改变一般 Action 的能力。后续能力必须先提供同等完整证明，不能仅因现有 Trigger enabled 即放行。

用户批准前复核当前定义，变化返回409 stale，旧未绑定 choice 返回409 incomplete，均不写 response。批准到持久 Event admission 间再复核，明确拒绝会记录 rejected delivery 和 dispatchError，而不是假 accepted；结果不明仍 unknown。Card/Event 保存的内部 Action 副本仅用于此类卡片，Engine 首次 attempt 和后续 attempt 校验绑定，执行该已绑定副本，并在每个 Session effect 前复核；不读取变化后定义来执行新目标。定义/权限变化允许明确失败，不更新卡片或扩大批准含义。没有通用 snapshot 生命周期，也不改变一般 Job 的动态 Action 引用或 Group pin。

SQLite 增量新增 bindings 列，旧记录默认 `{}`，不重写原定义/签名/response，也不猜测旧 choice 权限。旧卡片仍可读/拒收；没有可信绑定不可直接执行。旧 in_flight/unknown（含删除后的 detached）只查原 Event receipt，不再要求当前 Trigger 仍启用；缺回执保留 unknown。已有 Event/Run 的持久接纳与 effect 成功仍是两层事实。


## 无需回答终态（2026-10-25）

`interaction_card` 增加 `list_pending` 与 `no_answer_needed` 操作。前者按可信 stdio 创建者筛选未终态且无执行意图的原卡片，返回 cards（稳定 id/revision/status）及 nextAfter，limit 1..500；分页 after 延续当前原卡片 ID 游标语义，游标被删除时显式 not_found，调用者重新读取，不能猜测漏项。后者使用 card_id/expected_revision，与 submit/reject 在 SQLite BEGIN IMMEDIATE 事务中 CAS 竞争，只允许一个终态胜出。同一可信创建者重复标记幂等，删除后 not_found，原 requestKey tombstone 不复活。

新字段 producer 仅由 `/api/interaction-card-tools/create` 的本地 stdio caller 桥接身份写入，沿用 same-user loopback assertion（拒绝 Origin/Sec-Fetch 与远程 MCP），不是远程认证机制。owner 可以沿用既有创建范围，但 producer 权限不继承 owner、manager/managed 或请求自报 producerSessionId。旧 HTTP create/Job/router 及历史数据没有可证明创建者时 producer 保持 NULL，迁移不猜测来源，重试创建也不补绑。新增 producer 操作不接受 owner override，不获得用户 Bearer 或删除能力。

无需回答持久存放在 no_answer_at 字段，response/event 均为空，不产生 response/rejection outbox，不执行 choice，不取消既有 Action。任何 response/event 或 delivery 非 not_requested 均不能标记。冷重启保留状态，用户混排列表继续返回原卡片；Locker 明显显示“无需回答 · No answer needed”与 Read-only，移除回答/拒绝/选择/文本输入，仅留删除。旧 UI 或重放 HTTP submit/respond/reject 均 409；用户 Bearer 删除权限不变，不提供恢复。现有 read/unread 通用写入与“只允许删除”冲突，本实现禁止该终态的 read 写入，并从批量已读操作中排除；不擅自把无需回答解释成已读。

GUI 延续既有加载/手动 Refresh/窗口 focus 刷新机制；过期 UI 在 409 conflict 后读取原卡片并展示终态。无需回答不等于用户批准/拒绝，也不是 Trigger/Job 成功回执。


## 相关 user 上下文（2026-10-25）

两类 Locker 条目增加不可变 `relatedMessageId: string | null`。仅可信本地 MCP producer 新建时解析 owner Session 的上一条 user 类型 Pan messageId；self 取自己，MA 代建取 owner。非 agent 的 HTTP/Router/Job/internal 创建、旧行、owner 缺失、旧消息无 Pan ID或不能可靠判定均为 null。只读元数据不参与原签名/幂等键；原 requestKey 或 messageId+签名重复返回原字段，绝不补绑或覆盖。原 no_answer_needed、CAS、read、删除与 tombstone 不变。

服务端 `message_navigation.is_navigation_user` 等价于 `messageFilter.getNavigationKind == user`：report 来源/报告头优先排除，随后排除当前/旧 System 头、非 user role、结构化 agent 来源。不按当前 manager 关系或单纯 MA/QQ 字面前缀猜测来源。共用 navigationUserCases.json 在 Python 与 Vitest 两端验证分类（含 JS/Python 空白差异）；navigation API 的紧凑前缀裁剪也使用相同 ECMAScript 空白集合，避免 BOM/控制字符导致投影改变原消息类别。`filterVisibleMessages` 是客户端显示偏好，服务端不猜浏览器状态；Locker 使用同一函数检查当前可见性，被隐藏时禁用且解释，不跳到其他消息。

创建解析成本：热 Session 在 append/replace 共用 summary lock 下最多查看尾部 200 行和 1 MiB 文本；冷 Session 先用现有 metadata 访问器 `get(load_history=False)`，再 seek companion JSONL 尾部最多 1 MiB、解析最多 200 行，前后核对文件身份/大小/mtime。未知/不完整尾行、超限、无目标、legacy inline 历史均 fail closed 返回 null。不调用会扫描整段 JSONL 的 history_page，不 hydrate 或做 legacy backfill；冷 metadata 访问器仍沿用已有 Session 主文件读取成本。

Locker 的两类条目在正文上方显示带图标、可访问名称和 tooltip 的“跳转到相关上下文”。列表渲染、悬停、重渲染均不发导航请求；每次点击才通过与导航栏相同的 compact navigation 页与 classifier 解析一次可达目标；最多 200 页/100000 canonical 行，Session 已知删除/不可用或目标已知被显示设置隐藏时禁用；其他缺口、epoch/total 变化、目标移除或非 user 在点击失败后留在 Locker 给出原因，按钮保持可重试。仅向临时 relatedMessageStore 交接本次解析的 owner/id/offset/total，选择 Session 后打开 Chat；native terminal 显示切回历史视图，不更改 native 控制权。Chat 消费已解析目标，不重复请求导航，再调用与 MessageNavigationRail 共用的 jumpToMessage：ensureMessageLoaded(total-1-offset,total) 按原分页加载、验证实际 messageId、等待挂载并调用原 scrollToMessage 进行滚动高亮；原引擎负责虚拟列表定位。切 Session/卸载取消旧跳转，身份失效或无法渲染时显示明确错误，绝不按旧 offset 跳到另一条消息。此临时桥仅在内存交接本次点击的 offset，不持久化；实际加载后仍核对 messageId，不引入第二套滚动引擎。
# User → Session cards (2026-10-25)

This reverse flow is product-named “reminding card”. Its public HTTP/MCP surfaces use
`/api/reminding-cards` and `reminding_card`; the independent internal storage/module names
remain `user_cards` and `user_card_*` for compatibility, with no table migration. This reverse flow uses independent
`user_cards` storage and `user_card_*` reminder/outbox/settings
tables. It does not reinterpret fields in `cards` or enter its open/responded/reject/no_answer_needed
state machine. One `kind` (`question`/`task`) selects only whether an empty answer is allowed.
The creation Session is immutable owner; trusted stdio identity must equal owner for list/answer,
including when another Session is its MA. Stable create identity, attachment registry ownership,
revision CAS and first-answer terminal semantics are enforced at admission. No reject or overwrite.

The expanded SendQueuePanel action row's `create reminding card` button changes the current composer into card mode and closes the panel, retaining the existing uploader. The right-aligned Question/Task selector sits above the textarea; the send row uses `create` and `cancel`. Esc/toggle keeps the draft; Session switch clears the card draft. User Bearer
governs create/list/read/delete/download and Behavior's integer reminder interval (10, range 1–1440).

Unanswered records display waiting and no read control. First answer sets `answeredAt` once and
marks the card unread; `createdAt` is never overwritten. The UI keeps one record and projects an
answered card into two timeline positions, creation and answer. Each position sorts by its displayed
time, ties put original before projection, then deterministic card id; React keys are id+position.
Both rows show identical body, attachments, answer, both timestamps, direction and read state.
Read/delete actions use the plain card id and update/remove both projections immediately. Totals,
unread and pending reminder eligibility count distinct records. Unanswered cards have one position.

Creation durably records an immediate reminder intent containing fixed text, body and card id.
While owner has pending cards, a single service-owned async scheduler records one intent per interval;
future text is exactly “你还有未回答的用户卡片，如果目前仍无法回答，请忽略这条消息并朝着能回答的方向工作，但不要为了回答而随意回答”.
It persists last notification time, coalesces missed periods after restart and retries uncertain queue
admission with the same ID. Last answer/delete clears future reminder intents. With no pending work,
the scheduler sleeps on an event rather than polling. Notification admission uses normal durable
queue locking and Illegal staging, then invokes the normal queue wake/auto-spawn path. It never
force-clears Illegal or bypasses Pause/locks/leases. Illegal, Pause, message locks, readonly and lease gates remain unchanged;
reminders do not supply a user response or approval. No detached helper or new lifecycle owner.


## 多问题卡 v2（2026-10-25）

新卡可使用 `card.schemaVersion: 2`、`title`、可选 `body` 和 `questions[]`。
返回 Card 的 `schemaVersion` 同为 2。v1 的 choices/body/answerInput 卡保持原结构、原行为，
不迁移历史卡；分页包络和 Event 包络版本仍为 1，读取端逐卡识别版本。

每题为 `{id,prompt,type,required?,choices?}`：id 在卡内稳定唯一，prompt 最多 500 字；
type 为 single_choice / multi_choice / text；required 默认 true。choice 为 `{id,label,triggerRef?}`，
其 id 在题内唯一，可以在不同题复用。最多 20 题、每题 20 选项、文本答案最多 10000 个 Unicode 字符。
题目 ID 与选项 ID 区分大小写。题型和答案字段必须一致，拒绝未知问题/选项、重复多选和混合字段。

用户通过原 `/submit` 或 `/respond` 一次提交 `{requestId,expectedRevision,answers}`，
其中 `answers[questionId]` 为 `{choiceId}`、`{choiceIds}`、`{text}` 或 `{rejected:true,reason?}`。
必填题必须作答或拒绝；可选题可以省略。拒绝原因可选，上限 10000 字。
不存草稿、不局部写入；全部题目的校验与 response、交付意图在同一 SQLite 事务完成。
同 requestId、同 answers 的重试幂等；改内容、换 requestId 或跨入口覆盖原终态都冲突。

整卡 `/reject` 仍为 rejected 终态。所有题目都逐题拒绝时也为 rejected；
其余提交为 responded，可同时含正常答案、逐题拒绝与省略的可选题。
逐题拒绝不产生卡级 rejectionDelivery；全拒绝或整卡拒绝只创建一份卡级拒绝通知，
v2 无原因的整卡拒绝也发送通知。v1 保持原有无原因不发拒绝通知的行为。
生产者通过 get / receipt 和来源 Queue 通知读取完整 answers。

执行授权仍在整卡提交入口：创建时冻结 `trustedChoices[questionId][choiceId]`，
提交时对全部选中的执行选项复检，然后才记录整份答案。拒绝的题目没有执行选项。
每个选中 Trigger 有独立、稳定且含 questionId/choiceId 的 Event 身份和持久交付状态；
其中 response 保留完整 answers，单题选择不构成独立用户授权。
unknown/in_flight 只按原 Event ID 查回执，重启和删除后也不自动重发。
卡级 delivery 汇总各事件状态，accepted 只代表全部已接纳，仍不代表 Action 执行完成。

v2 完整 payload 有界为 2000000 UTF-8 bytes，以容纳 20 题完整 Unicode 答案。
Automation Bus 只为受信 core 交互事件显式扩大容量，EventStore 在该内部入口使用同一上限；
其他事件、插件及 v1 卡的默认 64 KiB 边界保持。全局 EventStore 总容量限制继续生效。

Locker 在卡内纵向展示每题控件、逐题拒绝/撤销和可选原因，所有必填错误就地提示；
仅一个提交按钮。提交后显示全部题目的答案/拒绝原因/可选未答状态，并隐藏编辑控件。
未读和容器统计仍按卡计数。浏览器截图验证使用请求拦截的构建产物，不能替代真实 HTTP 验收。
