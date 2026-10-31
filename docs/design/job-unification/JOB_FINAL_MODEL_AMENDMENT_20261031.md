# Job终局模型修订：冻结字段与运行时字段分层

日期：2026-10-31。状态：用户明确批准，属于 JOB_FINAL_MODEL_20261025 的现行修订。

用户裁决：统一 Job 可以允许部分字段作为运行时字段，但必须显式标注，行为由各自管理器负责。该修订覆盖原计划中“冻结”相关的泛化表述；不允许未经标注的隐式变更，也不降低未知效果安全边界。

## 字段分类

- `frozen`：Job/Action release、Step 与 Action 映射、输入、授权来源、执行参数、effect 身份及已发生的业务效果。Run 入场后不变；Retry 沿原冻结计划和原 effect 身份查询/恢复。
- `runtime`：只有字段 schema 与管理器明确声明的字段可变。每个运行时字段必须声明 owner、允许状态、并发/版本栅栏、生效阶段、审计记录和未知结果处理。运行时字段不改变已发生效果的 effectKey，也不能把未知效果变成可重发效果。

## 投递字段规则

- 发送前且尚未被队列或外部通道接纳：目标缺失可进入持久积压；恢复或用户明确切换目标后，由投递管理器在同一 reservation/fence 边界重新读取并选择目标。定时 assign/send_session 保留旧积压、补发和并发追加语义。旧路径发送时读取的可变正文也必须显式标注，不能把所有 Run 输入变成 runtime。
- 已被队列/外部通道接纳、已确认成功或结果未知/部分成功：保持原投递身份和原 target 查询；不得因 target 更新盲目改投。未知结果先 reconcile，不能用 runtime 字段绕过 unknown 安全门。
- 完成通知在发送前明确未入队且可证明未发送时，可保持 pending，由通知管理器按声明规则补发或在用户明确操作后改投；known-no-attempt 不得粗略归为外部效果未知。已 ack/not_applicable 的通知不因 runtime target 更新自动复活，除非另有明确操作契约。

## 实现和验收边界

运行时字段更新必须使用现有 operation/requestKey、CAS、审计和原 outbox/queue receipt；不得新增第二 dispatcher、第二 ledger 或隐式旧状态机。Job/Run 的冻结执行计划与运行时投递配置必须在 API、schema、UI 和 receipt 中可见地区分。迁移时旧字段具有 runtime 语义，必须保留该语义或向用户提出具体冲突；required target 校验、冻结 target 或 unsupported 不能代替功能保全验收。

这项修订允许在不改变已发生效果身份的前提下保留旧 target backlog/retarget 行为。各字段的 owner 和状态矩阵由对应 Action/Creator/通知管理器补充，不得跨管理器猜测或互相修改。

## 默认 runtime 字段矩阵（用于迁移和实现）

在用户已批准的分层原则下，以下是当前旧 scheduled-task、message 和 completion notification 的默认归属。若某个 Action 另有声明，必须在其 schema/release 中显式覆盖；不能由 mapper 猜测。

| 字段 | owner | 可变阶段 | 接纳后的规则 | 证明与栅栏 |
|---|---|---|---|---|
| `targetSessionId` / `targetSessionIds`（session.send/assign/fanout） | Creator/Delivery Manager | Event 已发布、Run 尚未取得 queue/external acceptance 的 pending/reserved 阶段 | 每个目标单独记录 acceptance；accepted/success/unknown/partial 的目标保持原身份，未接纳目标可按声明规则继续处理；目标缺失保留 backlog | Event/dispatch identity、runtimeRevision、reservation fence、逐目标 receipt |
| `text`（scheduled message/assign） | Creator/Delivery Manager | 同上，且仅限 Action 明确声明 `runtimeBeforeAcceptance` | queue/external acceptance 后冻结发送正文；不得修改已接纳效果 | Event/dispatch identity、text revision、receipt 中 effective stage |
| completion notification target | Notification Manager | terminal transition 已落盘、通知尚未入队且可证明未发送的 `pending` 阶段 | pending 可恢复或用户明确改投；accepted/success/unknown/partial 按原通知身份查询，不能盲改投 | outboxId、transitionSeq、target revision、not-sent/accepted receipt、CAS |
| completion notification payload（exitCode/logPath/creator/target 等） | Notification Manager | pending 发送前可补齐当前声明的展示字段 | 发送后保留原 payload；不因 target 更新重写已接纳通知 | outboxId + payload digest |
| QQ bot/target/text（若 Action release 声明 `runtimeBeforeAcceptance`） | Channel Delivery Manager | `call_api` 前且尚未取得 provider acceptance | provider acceptance/unknown 后按原 bot/effect 查询；禁止以 runtime 标记重发 unknown | effectKey、Attempt、provider receipt/phase |

`targetSessionIds` 的顺序去重不等于 retarget 安全证明；必须以逐目标 acceptance 状态判断。迁移 mapper 不能继续把这些字段无条件包装成 frozen literal：若旧实例含上述 runtime 语义，应生成带 owner/phase/receipt 映射的 runtime binding；无法无损表达时保留原记录并报告，不生成一个看似可执行但实际冻结错误的 Job。
