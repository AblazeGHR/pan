# 多 operation 合法性实施记录

基线 f3e051fc352fad21111cbbefc90ab2b6f63a4205；同一 review 分支，按 MA 授权三个阶段推进。只在本树实施，不合 main/practical、不 push/release、不操作既有服务或 8767/8768。设计依据为 PAN_MULTI_OPERATION_LEGALITY_PROPOSAL_20261018.md。

## 阶段一：ledger 与现有单 run 迁移

Session JSON 新增 legality_schema_version=2、operation_revision、execution_operations。每条记录保留不可复用 UUID、owner/generation、准入 revision、独立安全终态/退休证明；offline_run 保留前台兼容投影，不再独立决定合法性。terminal history/result 与 ledger/聚合仍使用同一 ordered writer 原子 replace；辅助责任不参与前台 seq/result/report。

同 owner 的所有未收尾操作一次分类、一个 carrier 保存消息成员，其余为空墓碑引用同 incident；后到 EOF 不扩大/重锁。继任 ready 仍只恢复捕获的前序批次；安全 Idle 存活 owner 仍拒第二 writer。v1 稳定 run ID 幂等迁移，v2 无 foreground 仍保持辅助责任 False。Stop 在确认 runtime 退休后按 owner 范围退休已登记责任；Illegal 继续等条件恢复。

实测：stage1-regression.xml 为 565 passed、0 failed/error/skip，48.823 秒，退出码 0；覆盖原 30 文件生命周期/队列/持久化及原生准入、完成、新聚合测试。最后两处调整（无前台指针 Stop、终态候选副本）后 stage1-final-focused.xml 为 51 passed、0 skip，退出码 0。两组重叠，不累加。首次定向测试暴露旧 Idle owner 保护缺失，已恢复并通过原用例。日志/JUnit 在 audit/legal-offline-multi-operation-20261018/。

阶段边界：此提交建立聚合基础；command/review/活动 compact/opaque 的真实入口尚未接入，不能将其描述为完整覆盖。阶段二、三继续实施。尚未运行本轮真实 CLI 或前端验证；本阶段未改前端。
