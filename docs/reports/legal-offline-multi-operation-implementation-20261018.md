# 多 operation 合法性实施记录

基线 f3e051fc352fad21111cbbefc90ab2b6f63a4205；同一 review 分支，按 MA 授权三个阶段推进。只在本树实施，不合 main/practical、不 push/release、不操作既有服务或 8767/8768。设计依据为 PAN_MULTI_OPERATION_LEGALITY_PROPOSAL_20261018.md。

## 阶段一：ledger 与现有单 run 迁移

Session JSON 新增 legality_schema_version=2、operation_revision、execution_operations。每条记录保留不可复用 UUID、owner/generation、准入 revision、独立安全终态/退休证明；offline_run 保留前台兼容投影，不再独立决定合法性。terminal history/result 与 ledger/聚合仍使用同一 ordered writer 原子 replace；辅助责任不参与前台 seq/result/report。

同 owner 的所有未收尾操作一次分类、一个 carrier 保存消息成员，其余为空墓碑引用同 incident；后到 EOF 不扩大/重锁。继任 ready 仍只恢复捕获的前序批次；安全 Idle 存活 owner 仍拒第二 writer。v1 稳定 run ID 幂等迁移，v2 无 foreground 仍保持辅助责任 False。Stop 在确认 runtime 退休后按 owner 范围退休已登记责任；Illegal 继续等条件恢复。

实测：stage1-regression.xml 为 565 passed、0 failed/error/skip，48.823 秒，退出码 0；覆盖原 30 文件生命周期/队列/持久化及原生准入、完成、新聚合测试。最后两处调整（无前台指针 Stop、终态候选副本）后 stage1-final-focused.xml 为 51 passed、0 skip，退出码 0。两组重叠，不累加。首次定向测试暴露旧 Idle owner 保护缺失，已恢复并通过原用例。日志/JUnit 在 audit/legal-offline-multi-operation-20261018/。

阶段边界：此提交建立聚合基础；command/review/活动 compact/opaque 的真实入口尚未接入，不能将其描述为完整覆盖。阶段二、三继续实施。尚未运行本轮真实 CLI 或前端验证；本阶段未改前端。

## 阶段二：独立 command 与 detached review

新增独立辅助准入及事件候选提交，复用真实 stdin ACK；主 run/task_seq/current result/report 不被辅助操作覆盖。每个请求拥有随机 admission/operation UUID、捕获 owner/generation 和 connection epoch/RPC/process 元数据。多 command 可与前台并发；原 command/exec deferred final exitCode 响应才是安全终态，write/terminate ACK 不结束原命令。发送 unknown 保留责任，明确拒绝只退休该 admission。

review/start detached 原参数不改；返回 reviewThreadId/turn.id 后先持久 bind，再按完全匹配的子资源 completed 收尾。辅助 peer 和主订阅均可观察子 thread；提前完成事件有 128 项有界缓存，溢出/冲突保留 uncertain。主 accumulator 不再误吞外国子 thread，已登记子资源只开放受限只读和准确 turn continuation。未证明完成不写 True。

实测：stage2-regression.xml 为 116 passed、0 skip/failure/error，14.414 秒，退出码 0；包含 command/review 新测试及原受控准入、native completion、passthrough、聚合和真实 native keyboard/compact。随后强化 connection 元数据与 provider 前持久证据断言后 stage2-auxiliary.xml 为 11 passed、0 skip，退出码 0；组间重叠不累计。

真实本地 Codex 0.162.1 + 临时 CODEX_HOME/workspace + 本地 SyntheticResponses：command/exec 真进程写文件并正常收尾；实验能力显式 historyMode=legacy 的 thread detached review 收到准确子 thread 终态且恢复 True，provider 收请求时磁盘已 False；historyMode=paginated 明确拒绝 detached，单项安全退休，未请求模型。产品默认 history mode/experimental capability 均未改；默认新 thread 的 detached 支持受 upstream 限制，不能声称它可成功执行。测试只为验证保留可用 legacy 路径，没有用改写 inline 冒充 detached。

真实子 peer 与 kernel 均使用临时自有端口/Job，finally 关闭；未触碰既有 Pan。此阶段仍不表示 active compact、opaque passthrough、takeover proof 已完成，阶段三继续。
