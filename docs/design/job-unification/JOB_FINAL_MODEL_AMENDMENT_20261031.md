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
