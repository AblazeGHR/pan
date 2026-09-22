# Bug record — selected Session transcript temporarily loses rows during summary refresh

日期：2026-09-22
范围：当前隔离 worktree 的前端 Session transcript/reconciliation 补修
分类：与 cold summary `historyTotal=0` / unknown card 回归分开；这是当前选中 Session 的历史窗口一致性缺陷。

## 现场记录

用户现场观察为：当前 Session 的大段既有历史暂时不可见，只剩连续 thinking 块和一条 assistant 文本；刚发出的 user 消息也可能消失；切换到其他 Session 再切回后消息恢复。证据截图路径已记录为：

`D:\project\Pan\data\attachments\ses_cf8fe1eaf6af6710-915f35db4241\upload_091098df072b4d839df71fde60b3a003.png`

本次审查没有打开、读取或修改该 practical attachment/data，也没有操作 8768、`Pan practical` 或 `Pan-main`。该现场缺陷不应归类为 Session 卡片的 cold summary 计数问题：卡片的 null/unknown 表示仍是另一条契约。

## 源码时序与根因

现场时序固定为：

1. 当前 Session 已有长的 `currentMessages`/loaded history。
2. 发送事务将 optimistic user 写入当前投影；随后 `worker.stream` 写入 thinking/assistant live rows，`worker.result` 对账 final，`worker.status` 收敛 idle。
3. 新增的 `session.summaryBackfillCompleted` handler 通过 300ms debounce 触发全局 `loadSessions()`；其它 session/result 事件也会共享这个 list refresh。
4. 修复前，`loadSessions()` 在 summary list response 返回后仍会把选中 Session 的 `history` 再喂给 `applyHistoryPageToState()`。兼容/陈旧 payload 只有尾部窗口时，它会把尾部当成 selected transcript 的权威页。
5. 另外，history window 的 epoch replacement 原先把“跨 epoch 且 revision 相等”视为足够新。延迟的旧 history page 因此可以替换整个 loaded window；`currentMessages` 随之只剩 thinking/final 尾部。切换 Session 会走 `selectSession()` 的 fresh history GET，所以看起来又恢复。

因此根因是“sidebar summary refresh 越权写 selected transcript”与“跨 epoch equal-revision stale page 未拒绝”的组合，不是 durable JSONL 消息内容被删除，也不是本次 cold summary count=0 修复本身。

## 当前修复

- `loadSessions()` 现在把 `summary=1` 限定为 session/card metadata reconciliation；不会用 list response 重建 selected `currentMessages` 或 transcript。
- 选中 Session 的已有 history、`historyStart`、`historyEpoch`、`historyRevision` 从当前 projection/transcript 保留；兼容 payload 中的 partial history 不能覆盖它。
- 真正的 history 仍只通过 `selectSession()`、`refreshCurrentSessionHistory()`、`loadOlderMessages()` 的请求序列和 window merge 入口更新。
- 跨 epoch 的 history replacement 只有在 `incomingRevision > currentRevision` 时接受；equal-revision 跨 epoch 响应标记为 ambiguous/stale 并拒绝。
- `session.summaryBackfillCompleted` 仍保留为卡片 metadata refresh trigger，因此 backfill 后卡片可收敛，但不会触碰聊天窗口；selected Session 真正不存在时仍保留原有清空选择行为。

## 回归测试

新增确定性 Vitest 覆盖：

- 长历史 + optimistic user + stream/result/status 与 `summaryBackfillCompleted`、session list refresh、history refresh、延迟旧 history response 交错；任何阶段不得丢既有行或新 user，A→B→A 后 `currentMessages` 的 role/content 序列一致。
- summary 兼容 payload 携带只有尾部两行的 history 时，不得替换 selected chat。

红测曾观察到 selected transcript 从 16 行缩为 `thinking + final` 两行；修复后两条新增场景均通过。现有 history/window/epoch、终态对账和 virtualizer stable-key 测试也通过；本缺陷的修复不改变 `ChatMessages` 的虚拟列表数据源，只阻止错误的 store window replacement。
