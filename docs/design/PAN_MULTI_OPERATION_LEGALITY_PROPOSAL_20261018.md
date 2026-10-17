# 多 operation 持久合法性：最小兼容设计（供 MA 决定，尚未实施）

日期：2026-10-18。冻结源码 `f39f0561c824fa6aa122957dd28eb69e50c77129`；同一 review 工作树/分支。本文只提出设计和改动面，不表示任何剩余入口已实现或验收。既有受控 turn/start 和空闲 /compact 的 337 测试证据继续独立保留，不在本轮重跑或累计。

## 1. 推荐结论

建议在现有 Session JSON + queue_lock + ordered writer 内加入**按 operation UUID 的持久执行记录**，保留各 operation 的独立 owner/资源绑定/终态，Session legal_offline 仅由所有尚负有安全收尾责任的记录聚合；不新增数据库/服务，也不通过串行化独立命令或禁用 passthrough 模拟安全。

技术上应分两层：

- **执行责任 ledger**：哪些 operation 已获准执行，哪些仍未安全退休；owner/generation/operation ID 是提交条件。
- **生命周期与展示投影**：Worker 的主 foreground turn、UI Idle、last_result、报告计数，以及 Session 聚合合法性。这些投影不能被某一个独立命令的完成回调整体覆盖。

command/exec 和 detached review 在当前协议有明确响应/资源身份，适合用统一 ledger 补齐。活动 compact 的精准归属存在协议歧义；未知方法也没有可普遍证明的完成语义。它们必须支持继续转发并保留 uncertainty，不能伪称收到 RPC response 就全部安全结束。

## 2. 冻结代码为何不能直接增加计数

| 冻结源码证据 | 实际限制 |
| --- | --- |
| legal_offline.py:19、:103–127 | FIELDS、begin/finish 只有一个 offline_run；finish 直接写 True。单个终态会掩盖并发责任。 |
| worker.py:2141–2208 | 准入写同一 offline_run，随后覆盖 Worker 的 _current_seq/_current_task_id/_execution_seq 等单份字段，并拒绝并发 active。 |
| worker.py:1553–1588、:2082–2114 | 任务终态写 last_result、Idle、history，然后保存单 run 的 finish；没有 per-operation 身份。重复终态路径 :1374–1381 同样会 finish 当前 run。 |
| worker.py:6290–6297 | handoff 拒绝也调用单 run finish；多 operation 后只能取消该次尚未交接的操作，不能清其他责任。 |
| codex/app_server_wrapper.py:251–277、:1488–1523 | 一个 _native_admission、一个 external accumulator，无法同时关联多个命令/processId/子 thread。 |
| codex/resident_tui.py:144–158、:249–283 | 主受控 RPC 有 id→admission，但活动 compact 仍直接转发；独立 command/exec 不走准入。 |
| codex/resident_tui.py:183–186 | 辅助 peer 的普通通知被忽略，原意是避免主订阅重复。detached 子 thread 不能假定已在主订阅观察到。 |
| legal_offline.py:157–179 | activate 会基于单 run 重建 Idle；多 operation 时 ready 不能顺带清仍存活的命令或未知执行。 |

单独 active_count 不是权威数据：ACK 丢失、重复 terminal、服务重启、后到的 bind 事件都会让计数漂移。计数只能由 ledger 在同一 writer 内计算。

## 3. 数据形状：在现有元数据中加三项

示意字段名可随实现调整；关键是责任记录和 owner ID 不可重用：

```text
legality_schema_version: 2
operation_revision: monotonic integer
execution_owners: ownerId -> {
  ownerId, serviceEpoch, workerToken, workerGeneration,
  runtimeIdentity, connectionEpochs, parentOwnerId?,
  resourceScope, retirementState, retirementProof?, coverage
}
execution_operations: operationId -> {
  operationId/admissionId, admittedRevision, kind,
  ownerId, capturedControlGeneration, origin,
  phase: admitted | bound | running | uncertain | terminal,
  bindings: [{connectionEpoch, rpcId?, processId?, threadId?, turnId?}],
  terminal?: {terminalEventId, outcome, safe, proofKind, committedAt},
  incidentId?, retirement?: {safe, proofKind, proofId}, coverage
}
```

- operationId 可直接复用现有不可重用 run UUID/admission UUID 中的一种作为权威键；旧协议两个字段若继续保留，必须为不可变一一映射，不能重新分配 run。
- ownerId 表示受控 runtime/执行域，而非“最后完成的 operation”；同一个 app-server 的多 operation 可共享 kernel owner，但每条记录必须独立引用其 owner 和连接 epoch。detached thread 是该 owner 下的独立资源，不能冒用原 thread ID。
- rpcId/processId 都可能重用，必须以 connectionEpoch 为域；threadId/turnId 也须带 provider owner scope。它们只作绑定，不能取代 operation UUID 的幂等键。
- origin 记录 foreground task/report 的关联 ID；独立 command、compact、review 不覆盖 Worker 的主 _current_*，不凭空消费 queue，也不重复增加 assigned-task doneCount。
- 新增全局 operation_revision 用于准入先后和恢复边界；辅助命令不必占 task_seq。真正的 Pan 主任务/原生 foreground turn 保留既有 task_seq 合同。
- offline_run 可暂留为 legacy foreground 的兼容指针/投影，但所有合法性写入都改成按 operationId。不能长期维持两个权威；legal_offline 只在 ledger 候选提交时聚合保存。

## 4. 聚合不变量

定义 outstanding 为已持久准入且**尚无安全终态或安全退休证明**的 operation；包括 admitted 尚未发、bound/running、发送 unknown、未完成取消、未完成事故对账。

```text
legal_offline = (outstanding is empty)
```

这里的 True 不能由 UI Idle、RPC 空响应、某一个 turn 完成、live_count==0 或 owner root PID 消失推导。

- T1 与 C1 同时运行；T1 安全完成，只结束 T1。C1 未完成时 legal_offline 仍 False。C1 安全结束后才 True。
- 正常捕获 Error/429、明确拒绝或“确定没有发送”均可形成该 operation 的安全终态；不能将其他 operation 顺便标 safe。
- 操作失败记录不删除、不伪造 safe success。若已发生 Illegal，先封存事故快照/批次，确认相应 owner 退休，并完成当前策略要求的恢复/补偿，才能另记 `retirement.safe=true`。原 outcome 仍 failed；未完成安全退休的历史失败仍是责任，不让新 ready 直接擦掉。
- legal_offline=False 仍不等于 Illegal。运行中或结果未知都可为 False；只有明确事故检测/死亡证据形成的 incident 才进入 Illegal 投影。
- retained history/tombstone 不永远阻止合法性；判定看未退休责任，而不是“历史上有没有失败”。无法证明退休则继续 False，不为了清历史把它删掉。

owner lease 与执行任务不是一回事。无任务的合法 Idle 可以有仍活的常驻 CLI；没有尚未收尾 operation 时 True，但 owner gate 继续阻第二 writer。未知外部 owner 的特殊覆盖边界见第 9 节。

## 5. 原子操作 API 与事件协议

建议在 legal_offline.py 提供以下小型纯候选变更，worker/session 继续拥有持久化：

- admit(operationId, ownerId, expectedRuntime, kind, origin)：同次写 operation 与 aggregate False，提交后回 ACK。重复同 ID 查已有回执，不增 operation、不重发 provider。
- bind(operationId, expectedOwner, binding, bindEventId)：将新 turn/process/thread 映射纳入同次保存；绑定冲突只把对应 operation 置 uncertain，不覆盖别人。
- finish(operationId, expectedOwner, terminalEventId, safeProof)：只结束该 ID，重算 aggregate。foreground history/result/outbox/terminalKey 与其 finish 仍同次提交；辅助 operation 保存其 metadata 终态，不触发 foreground _task_done 或 assigned-task report。
- mark_uncertain、classify_owner_failure、retire_owner：按 owner/scope 批量处理其 operation，不能修改其他仍活 owner 的责任。

ACK/事件至少含 operationId、ownerId/owner epoch、requestId、operation_revision；resource 绑定含 connection epoch。终态事件有独立 terminalEventId。延续已完成的 stdin ACK 直达分发和 owner/thread/generation 发送前复检。

wrapper 的 _native_admission 改为 operationId→admission 字典；RPC `(connectionEpoch,rpcId)`、processId、thread/turn 分别有反向索引。主 thread 独占 turn 约束继续遵守 provider；独立 command 和 detached thread 不因主 turn active 被拒绝。不能用一把全 Session busy bool 替代资源级调度。

事件可能先于 response：先保存通用准入责任，通知进来按资源缓存，bind 成功后去重归并。缓存有上限，溢出进入明确 uncertain，不丢事件后标合法。崩溃前丢失绑定时，原准入仍为 outstanding。必要的 terminal ACK 可用于 wrapper 释放重试记录；不必延迟向原 TUI 转发 provider 的业务 response，但 Session 的 True/完成事件必须等安全保存。禁止重试 provider 业务请求来补回丢失终态。

Stop、Restart、正常 shutdown、queue 交接拒绝、幂等终态重播、ready/recovery 都必须使用指定 operationId 或已验证 owner scope，不再用当前 Worker 的 mutable executionSeq 结束“当前所有执行”。

## 6. 三类已知剩余入口

### 6.1 活动 turn 内 compact

准入必须发生在原有 peer.send 之前，即使主 turn 正在 Running；独立 compact operation C 不能借用主 T 的终态。T 先完成时 C 仍 outstanding，可继续保持现有请求转发时机，不强行等待 T 才把 compact 发给 provider。

**不能猜测的协议边界：**本机 0.162.1 schema 中 ThreadCompactStartResponse 是空 object；ContextCompactedNotification 只有 threadId/turnId，且已 deprecated，建议看 contextCompaction item；该 item 只有 id/type，没有 origin RPC id。Turn 也不含这个 compact 请求的 admission ID。活动 turn 可能本身自动 compaction，多个请求还可能排队/合并。不能把“第一个后到的 compact item”或“下一个 turn 完成”默认归给 C，也不能凭 FIFO 将多个 UUID 全部安全结束。

推荐最小兼容实现分两档：

1. 始终在转发前持久 C；存在可证明的 provider 返回资源/事件关系时绑定并结束 C。空 ACK 只表示接受，不结束 C；无法唯一关联则保持 C uncertain。原请求继续转发，不关闭功能。
2. 要完全自动收尾，需验证安装版本确有稳定关联语义，或 provider 最小返回 accepted operation/turn ID、回显 request ID。若没有，不应偷改成排队到 Idle 才转发；那会改变交互和执行时机，须 MA 明确选择。

即使暂不能精准收尾，先持久 C 已能防 T 完成后过早合法化；这是安全提升，不等于 complete。不能用瞬时 thread idle 或一次 thread/read 当队列已排空证明。

### 6.2 独立 command/exec

本机 schema 明确独立命令无需 thread/turn，command/exec 响应在进程退出且该连接 outputDelta 已发完后返回，结果有 exitCode/stdout/stderr。

- 转发前持久 C，关联原 connectionEpoch/rpcId；有 processId 则登记该域内的 processId，缺省 buffered 命令仍可靠 rpcId→C 关联，不能臆造跨连接可查询 PID。
- 多个 command 并行可各有 C1/C2，不占主 turn admission，不改已有 passthrough opt-in。后续 write/resize/terminate 只作已登记 process 的续操作，不重复建立执行任务。未知 processId 的原请求保持既有转发合同，但要保留未关联不确定性，不能清其他 C。
- deferred final response 作为正常结束证明，exitCode!=0 是正常已捕获错误，不自动 Illegal。terminate/write 的 RPC ACK 不是原进程退出，必须等原 command final response 或可验证进程退休。
- 丢连接、响应丢失时 C 仍 outstanding。若 provider 无跨连接查询能力，不能靠重新发送 command/exec 恢复；确认 owner 内所有该命令资源结束或保持 unknown。
- Session 终态保存不能写 Worker 全局 Idle/last_result/_task_done 并结束主 turn。独立命令结果留 operation terminal metadata，原 stdout/stderr 继续交给原生 TUI，不强行作为 Agent assistant 回复/任务完成报告。

该入口可在同架构内可靠实现正常路径和并发；断线恢复仍依赖 owner/资源退休能力，但不会再让一个命令结束掩盖其他命令。

### 6.3 detached review

本机 schema 的 review/start response 必须返回 reviewThreadId 和 turn；delivery=detached 在该版本虽 deprecated 但仍支持。保持原 delivery/target，不擅自改写成新 thread/start + inline review。

- 转发 review/start 前建立 R，先绑定原 request/connection/owner。新子 thread 尚未知不妨碍事前 False。
- response 返回后原子登记 reviewThreadId/turn.id 及 R 的 owner scope。响应之前已到的通知缓冲并关联；响应丢失且未知子 thread 时保留 R unknown。
- 当前辅助 peer 会丢弃普通通知（resident_tui.py:183–186）；需要按连接+已登记资源路由到 operation 事件收集器，再按 threadId/turnId 去重，而不是全部镜像给主 thread 的 external accumulator。
- 原 session 的主 thread 限制保留；仅 provider 对本次 R 返回并已持久登记的子 thread 取得受限 continuation/read/interrupt 路由资格。不能为了 detached 开放任意外部 thread。
- review start 响应表示已建立 review，不是 review 已完成；完成必须取返回的准确 thread/turn 的 terminal。主 thread 完成、TUI 切回 Pan、review RPC 返回都不能结束 R。
- 本机 schema 有 thread/read/loaded/list/unsubscribe，未找到单独 subscribe 方法；不能声称 thread/read 一定建立完整通知订阅。优先复用发起 review 的原 peer；必须用本地真实 CLI+本地 provider 验证 detached 完成通知在哪个连接交付。若连接丢失，可做无执行副作用的精确资源查询验证 terminal，不能 resume 子 thread 启动第二 writer 来找结果。

## 7. 未知 passthrough：保留转发，但不虚构完成证明

建议使用“语义分类表 + opaque liability”，不使用执行方法黑名单。既有 passthrough opt-in、参数、响应和 RPC 失败返回原样保留；新方法不会因为名字未知被一律禁用。

| 类别 | 准入/结束规则 |
| --- | --- |
| 经版本契约验证的只读观察 | 不创建执行 operation；保持现有 thread/权限范围。名字以 read/list 结尾不足以作证明。 |
| 经验证的纯设置写入 | 原 response 确认设置提交即可结束其短操作；既有 native_settings_uncertain 独立保留，设置成功不等于清执行责任。不能把会启动/重载工具进程的 config/mcpServer/reload 自动算纯设置。 |
| 已知执行起点 | 根据 turn、command、review、compact 的生命周期规则建立独立 operation。 |
| 已知续写/审批/取消/resize | 绑定既有 operation/资源，不重建 run；原 RPC 可成功不等于父 operation 已终止。 |
| 无法识别的 RPC | 转发前建立 opaque operation，保留 private request/connection 归属；普通 response 仅证明 RPC replied，不能默认后台执行也完成。通过后续语义分类/资源 terminal/owner 安全退休再结束；无法证明则继续 uncertain。 |

opaque operation 会使 legal_offline 在看似 UI Idle 时仍 False，但 False 不自动 Illegal；含义是“尚不能证明安全收尾”，不是把合法正常 Idle 一律判异常。它不增加持续锁未来队列的消息门禁；既有 native ownership/lease/未知交接规则继续独立生效。操作仍可转发，同 owner 下资源已知的其他 operation 可按原规则工作；unknown owner 仍拒第二 writer。

这是一个必须向 MA 明确的代价：在“未知方法可能启动任意后台执行、继续允许转发”的前提下，无法同时保证仅靠 RPC reply 自动恢复 True。若产品要求 reply 后必 True，则须得到方法不会留下执行的契约；否则不能实现完整安全保证。设置的已知语义可按安装版本新增，不应靠定时器、sleep 或 UI idle 消除 opaque 责任。

## 8. 异常批次、恢复和迁移

### 同 owner 崩溃的一次快照

复用已有 queue exclusion，先对账失主 pending/ledger，再把该 owner 的全部 unresolved operation 一次分类并形成事故 cohort。不能等每条 operation 的迟到 EOF 分别 Lock All，否则第一次后新入队 D 会被第二条当作“新异常”扩大锁范围。

推荐保持已有“失败 run UUID”幂等 key：在同一事务内为本 cohort 每个 operation 建批次/墓碑，按 admittedRevision 选择一个确定 carrier 记录实际新增成员，其余为空批次并引用同 incidentId。精确成员、来源、pending/ledger/revision、全部 operation 的事故状态同时保存。后续回调只查询该持久 cohort，不重做快照。旧单 run 的 batch ID 不变。

若一个 operation 在 owner 仍有效时独立发生可证明的不安全终止，则是不同 incident，单独分类；仅失去响应而 owner/资源仍未知时不假造死亡。不同 incident 不夺取已有 manual/illegal，手动释放不重锁，报告策略和未知安全 gate 不受解锁影响。

### Ready、退休和原队列

继任 ready 捕获 operation_revision/前序 incident 集合，仍只按 batch 条件恢复历史 illegal 锁；不清仍活或未知的 C/R/O，也不将 aggregate 无条件写 True。旧失败必须先有事故持久快照、owner 安全退休/责任对账，才能记 safe retirement。恢复批次和责任结算同次保存；边界后的事件不解。

全锁不自动 spawn 的规则保持，新未锁消息/用户 Start 仍按既有 lane 和 owner 安全条件建立有效实例；不能绕过仍存活的 detached owner 建第二 writer。手动消息 Unlock 不能关闭 operation/opaque/owner 责任。

### 幂等迁移

- JSON v2 构造/校验必须先识别 execution_operations，不能走 session.py:1567–1577“无有效 offline_run 就初始化 True”的旧分支，否则会清掉多 operation 的 False。
- 有稳定旧 run UUID 的 running/illegal 导入同 ID 的 legacy operation，保留原 owner、batch ID/空墓碑与用户锁；安全 Idle 可保留安全历史或空责任集合。
- 无可靠身份的旧历史继续记录 migration uncertainty，不虚构 operation/事故，也不改变队列可消费性；外部 unknown owner 仍独立保留。
- 重新加载不重分 UUID、不覆写新绑定/终态。不从 last_legal_worker_state 持续双写，旧恢复意图独立。
- active/uncertain/事故未对账项不得按数量裁剪；可按已退休 owner fence 清理旧终态详细内容，但事件墓碑/批次幂等信息必须保留，不能因截断让旧回调复活。

## 9. 外部 takeover：实际退休与合法 Idle 共存

冻结实现已在 server.py:3456 打开终端前持久 unknown owner；这不是 per-turn admission，也不是“外部 TUI 一定 Running”。此前已安全结束的已知 operation 可以保持 legal_offline=True，外部 owner 仍活/未知则禁止第二 writer。建议增加 `coverage=external-owner-only` 投影，明确 True 只说明已登记责任已安全结束，不能据此声称不可观察的外部 turn 已有完整保证。

**同一服务进程内的可执行退休路径：**用户 Stop/Restart → 现有 session spawn lock → 停止 takeover Job → TakeoverJob.stop 的 TerminateJobObject + ActiveProcesses==0（takeover_job.py:61–76）→ 在 ordered writer 内记录 owner retirement proof、结束该 owner 下可被该证明覆盖的 operations/opaque 责任、重算 aggregate → 成功后再释放控制记录/允许新实例。进程已被停止不等于需要伪造每项“正常完成”；terminal outcome 可为 stopped，safe proof 表示已安全终止并保存必要信息。

现有 worker.py:7249–7288 会在 Job stop 后先清 runtime handle，再 _claim_offline_owner；若此时保存失败，第二次调用可能只剩持久 unknown 标记，仍会阻第二 writer。新设计应保持退休 proof/Job 对象可重试到持久提交成功，不能因 save 失败丢失确认来源。具体需拆分 terminate-and-verify 与 close-after-commit：当前 TakeoverJob.stop 内部已 CloseHandle，仅保留一个 handle=None 的 Python 对象不足以复检；可在内存保留相同 owner scope 的已确认 proof 并重试保存，或保持 Job handle 至提交成功后释放。服务若在 proof 保存前崩溃，仍保持 durable unknown，不凭丢失的内存证明自动恢复。先杀、保存失败时只允许重试退休，不创建新 writer。

**非 Job 终端：**现有路径验证当时观察到的 root/后代 PID-create-time 全死；root 已消失或访问受限时拒绝推断。这个快照不能证明未观察到的脱离后代。不能仅凭 root 不存在就结束所有 operation；应保留 scope 不完整/unknown，或引入覆盖全部后代的受控 owner/hook 后再提供完整证明。

**服务崩溃后：**持久 unknown 预留、丢失的 Job handle、甚至旧空 PID 列表都不能证明外部执行退休。Windows kill-on-job-close 是清理机制，不是已保存的逐项退休回执；要自动对账需可独立核验的 owner/资源证据。现阶段实际安全行为是保持 unknown、拒第二 writer；操作者独立确认全部相关执行退休后，以受控且可审计的 owner retirement 动作落盘，不直接编辑布尔合法性。自动重连/退休工具需要后续实现，不把它写成已有 API。

要真正覆盖外部每次任务，仍需把该终端接到同一受控代理或等价同步 hook；owner gate 与 safe Idle 共存并不能替代这一点。若 MA 保留当前外部模式，本需求的覆盖范围必须显示 partial，不把未登记的运行猜成某个 Illegal run。

## 10. 建议改动面与实现顺序

| 文件/模块 | 必须变化 |
| --- | --- |
| core/session.py | v2 字段/迁移/序列化/summary revision；原子 override 包含完整 ledger/aggregate，避免无旧 run 时误初始化。 |
| core/legal_offline.py | admit/bind/finish/retire/aggregate 纯变更；事故 cohort 一次快照；activate 不清活操作。 |
| core/worker.py | 所有开始/终态/重播/交接拒绝/Stop/Restart/shutdown 改传具体 op ID；foreground runtime 与 auxiliary metadata 分离；gate 按资源与 owner，不按全 Session active bool。 |
| codex/native_admission.py | 保留单 operation 的发送状态保护，扩展 bind/terminal event ID 与重试回执；不要把所有并发操作共享一个对象。 |
| codex/app_server_wrapper.py | admission/collector 字典、资源索引、多 accumulator、原始连接 route；普通 Pan queue 正常复用相同 ledger 协议。 |
| codex/resident_tui.py | 每个执行 RPC 事前准入；同连接 command final response、子 thread 通知路由、compact 歧义保留；passthrough 分类但不禁未知。 |
| core/takeover_job.py 与 web/server.py | 退休 proof 与原子保存衔接；owner scope 明确，保持现有终端功能；未决定前不增加新服务。 |
| HTTP/WS summary、React（仅需要展示时） | 继续 authoritative legalOffline/illegalOffline；可加 coverage/activeOperationCount，不以计数作真源。涉及 React 后才 prepare_validation_env/相称前端验证。 |

建议 MA 按 3 个可审查阶段授权：① ledger/聚合及所有现有 finish 路径迁移；② command 并发与 detached routing/完成证明；③ active compact 和 unknown opaque 的完整对账、takeover 退休持久 proof。每阶段保持现有单锁和 owner gate，不将尚未完成阶段描述为全覆盖。

## 11. 验收矩阵（尚未执行）

- T+C1+C2+R 并发，逐项不同顺序结束，任一未安全退休则 False；normal Error/429 只结束自己的 op；重复/旧 terminal 不影响别人。
- C1/C2 具有相同 processId 但不同 connectionEpoch；一条连接断开/重连，不错认其他命令，terminate ACK 不能结束原 command。
- 主 turn 结束、compact 仍排队/活跃；同时自动 compaction/多个手动请求/合并请求，无法归属的 C 保持 uncertain，不能清所有项。
- detached response 前通知、response 丢失、子 thread terminal 走不同 peer、跨 thread 同名 turn，以及主 thread 结束，均不提前结束 R；本地真实 CLI+fake provider 验证通知范围。
- 未知 passthrough 的普通设置型/异步执行型 fake RPC 都能继续转发；只有有完成契约的操作自动结束；opaque reply 之后仍未完成则 False，但不自动 Illegal 或锁未来新消息。
- 每种 admit/bind/terminal/retire 写入失败、取消、ACK 丢失、迟到事件、writer 等待时 generation 替换、服务崩溃，确保 ledger 与 aggregate 同次可见、业务请求不重投。
- 同 owner 多 operation 一次死亡只形成一次 cohort 快照，第一检测后入队 D 不被后到 EOF 加锁；manual 转换/Unlock、报告策略/Pause/lease/未知交接 gate 保持。
- 继任 ready 只恢复前序已对账事故批次，不清仍活 detached/command/opaque；全锁多次 watchdog 不 spawn。
- v1→v2 反复加载、非法/缺失旧字段、已有新 operation、旧合法 Idle 和 unknown external owner，不覆盖新数据、不误判/改变可消费性。
- takeover Job 停止确认但保存失败再重试、root 先退出/后代存活、权限未知、服务崩溃丢 handle，保证不放第二 writer；safe Idle + owner alive/unknown 独立展示。

## 12. 本轮证据与 MA 决策点

本轮仅阅读冻结源码、既有本地生成 schema 和 Git 状态，保存协议摘录/源码 SHA256；未运行产品测试或服务，未 build/安装依赖，未操作 8767/8768、main/practical 或 MA integration 树，未回滚或应用任何归档 WIP。证据目录：`audit/legal-offline-multi-operation-design-20261018/`。

MA 需要决定的具体语义是：未知 RPC 保留转发时是否接受 opaque responsibility 持续 False；无法关联的 active compact 是否允许安全保持 uncertain，还是要求 provider 返回更精确身份/批准改变转发时机；外部 takeover 是否保留 owner-only partial coverage，还是扩展到受控代理。推荐先实现可证明的 ledger 聚合、command 和 detached 路径，保留这些缺口的真实状态，而非以未授权的阻断或过早 True 结束需求。
