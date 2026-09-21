# AUDIT_HISTORY_QUEUE — sessionStore / history / queue / Steer 一致性复审

- 基线：`591367a65f88e9d5270e8e99920ef5448d1d68c9`（`fix: unify Codex tool live and history projection`）
- 前序修复：`931f1cc`（`fix: enforce stage3 frontend history and delivery consistency`）
- 隔离 worktree：`D:/project/pan-worktrees/frontend-reaudit-history-ds-20260921`（`git rev-parse --show-toplevel` 即本目录，已核验）
- 复审日期：2026-09-21
- 边界：**未改任何产品代码 / 未改其他 worktree / 未 commit / 未 merge / 未 push / 未启动服务 / 未访问 8768 / 未用浏览器**。新增产物仅：3 个调查测试文件 + `evidence/` + 本报告。

> 本报告与旧报告 `t062-11-readonly-audit-20260920/FRONTEND_READONLY_AUDIT.md` 的关系：旧报告针对 `99f5c88` 的 `inferDivergedLiveMessages` / 全历史文本前缀匹配 / queue 无版本门禁 / Steer 无草稿事务等机制。`931f1cc` 已重写这些位置（见 §7）。本报告只记录**重写后新引入或仍存在、且本轮已实际复现**的机制，不重复旧结论。

---

## 0. 结论摘要

`931f1cc` 确实修好了旧报告的**队列版本门禁**（F07）与 **Steer 草稿事务**（F10），本轮用确定性用例验证通过（§5）。但它同时引入了三处**新的历史/顺序缺陷**，并且 `historyEpoch` / `historyRevision` 这个新协议字段**只写不读**：

| ID | 严重性 | 结论 | 证据等级 |
|---|---|---|---|
| **F-A** 分页窗口起点被尾页刷新覆盖 → 历史重复+错序 | P1 | 确定复现 | E（Vitest R1/R2 失败） |
| **F-B** live→result→history 重建：多块 turn 重排、**连续 DONE 轮丢块** | P1 | 确定复现 | E（store R4/R5/R7/R8 + 真实 useWebSocket 管线 E2/E3/E4） |
| **F-E** `applyLiveStream` 缓存投影下标不校验 role/身份 → 覆盖 user 行 | P1 | 确定复现 | E（Vitest R9） |
| **F-C** `historyEpoch`/`historyRevision` 不参与任何 gate；旧 epoch 页覆盖、epoch 替换保留旧 tail | P1 | 确定复现 | E（Vitest R3/R12） |
| **F-F** 无 provider id 的行按 `${role}:legacy:${content}` 归并 → 同文本新回复被吞、result 重复 | P2 | 确定复现 | E（Vitest R10/R11） |
| F-D 旧 epoch/短 history 触发根因相同的**页序反转** | P2 | 静态推断（未单独断言，可达性较弱） | S |
| 队列 ACK/tombstone/编辑/失败恢复 | — | 现状正确，本轮验证通过 | E（6 条正控全 PASS） |
| 历史同文本合法重复（带稳定 ID） | — | 现状正确，不按文本去重 | E（正控 R6 PASS） |
| Steer 草稿事务 / 业务错误抛出 | — | 现状正确 | S（静态；见 §5） |
| 冷重启/真断线/gap 恢复、真实 provider | — | **未验证** | — |

> F-B / F-C / F-A 与本批另一 TA（`t062-11-frontend-comprehensive-audit-astra-low-20260920`）的独立探针结论一致：其 `audit/probe-current-main.cjs` 在**同一 HEAD** 上用真实 Zustand 得到相同 observed 值（见 `audit/evidence/current-main-probes.json`）。本轮把其中 8 个快照迁成正式 Vitest（R7–R12、E4 等），observed 与本报告逐字一致。

### 实际命令与退出码

| 命令（cwd=`packages/web`） | 退出码 | 结果 |
|---|---|---|
| `pnpm install --frozen-lockfile --offline` | **0** | 用共享 store 离线装好依赖；`git status` 无跟踪文件变化 |
| `pnpm exec vitest run --reporter=dot`（**基线，3 个新测试文件移出后**） | **0** | 68 files / 586 tests 全过 |
| `vitest run`（3 个调查文件 v1：R1–R6/E1–E3/Q1–Q6） | **1** | 3 files / 15 tests：**7 failed / 8 passed** |
| `vitest run`（3 个调查文件 v2：+R7–R12/E4） | **1** | 3 files / 22 tests：**14 failed / 8 passed** |
| `vitest run`（全量，v2） | **1** | 71 files / 608 tests：**14 failed / 594 passed**（失败全部来自本轮 3 个新文件） |

日志：`evidence/00-baseline-no-new-tests.log`、`01-investigation-tests.log`（v1 verbose）、`02-full-suite.log`（v1 全量）、`06-reproducibility.txt`、`07-investigation-tests-v2.log`（v2 verbose）、`08-full-suite-v2.log`（v2 全量）。

---

## 1. 交付的调查测试（原始证据，失败测试保留）

| 文件 | 用例 |
|---|---|
| `packages/web/src/stores/reaudit.history.test.ts` | R1 窗口起点 / R2 分页重复 / R3 epoch 门禁 / R4 result 后顺序 / R5 刷新后重复 / **R6 正控** 同文本合法重复 / R7 多块 turn 顺序 / **R8 连续 DONE 轮不丢块（优先）** / R9 缓存投影下标越界覆盖 / R10 无 id 同文本被吞 / R11 无 id result 重复 / R12 epoch 替换去旧 tail |
| `packages/web/src/hooks/reaudit.orderedEvents.test.tsx` | **E1 正控** 两轮有序 / E2 result 后工具块错位 / E3 刷新后工具行重复 / **E4 连续两轮 DONE 位置（优先）** |
| `packages/web/src/stores/reaudit.queue.test.ts` | Q1–Q6 队列正控（ACK 迟到 / tombstone / 编辑 / 删除失败 / 重排 / 同版本幂等） |

失败清单（`evidence/07-investigation-tests-v2.log`，14 failed / 8 passed）：

```
× R1  expected 100 to be +0
× R2  expected ['m-50',…(197)] to equal ['m-0',…(146)]
× R3  expected 'eOld' to be 'eNew'
× R4  expected ['assistant','tool'] to equal ['tool','assistant']
× R5  received 4 rows, tool line twice
× R7  ['user:question','assistant:final','assistant:analysis','tool:tool']
× R8  ['user:question','assistant:final','user:question2','assistant:second final',
       'system:DONE-1','assistant:second analysis']   ← 6 行，'analysis'/'tool' 丢失
× R9  ['assistant:old-history','assistant:live updated','assistant:live']  ← user 行被覆盖
× R10 2 行，第二条 'same reply' 被吞
× R11 ['user:question','assistant:final','assistant:interim','tool:tool','assistant:final']
× R12 ['user:replacement','assistant:old answer']  ← 旧 epoch tail 残留
× E2  ['assistant:Hello world','tool:Command(…)']
× E3  ['user:u0','tool:…','assistant:…','tool:…']
× E4  ['assistant:Hello world','assistant:Second answer']  ← 'tool' 整行丢失
✓ R6, E1, Q1–Q6
```

> 这些断言写的是**正确契约**，不是现状快照。**生产代码未改**（`evidence/05-git-state.txt` 仅有 3 个新 untracked 测试文件）。

---

## 2. F-A · 分页窗口起点被尾页刷新覆盖 → 历史重复 + 错序（P1，确定复现，E）

### 代码位置
- `sessionStore.ts:1115` `refreshCurrentSessionHistory`
  - `1131-1141`：`previousWindowStart` 取 `historyWindowStarts[sid] ?? Math.max(0, data.total - previousHistory.length)`
  - `1181-1182`：`historyLoadEnd: data.start` 且 `historyWindowStarts[sid] = data.start` ← **根因**
- `sessionStore.ts:1046-1047, 1098-1099`：`selectSession` 同样把 `historyLoadEnd/historyWindowStarts` 设成 `data.start`
- `sessionStore.ts:1222, 1249-1250`：`loadOlderMessages` 用 `historyWindowStarts[sid]` 作为 `previousWindowStart`，并把窗口重设为 `data.start`
- `sessionStore.ts:659-718` `mergeHistoryPageByWindow`
  - `678` 前置分支 `pageStart + page.length <= previousWindowStart`
  - `688-696` 只按 `explicitMessageIdentity` 命中
  - `711-715` 未命中时的 push / splice 兜底

### 精确最小事件序列（无并发、无乱序、单 WS 顺序也复现）
1. 会话 A 有 150 条 canonical：`m-0(user) … m-149(assistant)`，`historyTotal=150`。
2. 已加载前 100 条：`currentMessages=[m-0..m-99]`，`historyWindowStarts[A]=0`，`historyLoadEnd=0`。
3. 触发 `refreshCurrentSessionHistory()` → `fetchSessionHistory(A,0,50)` 返回 **`start=100, history=[m-100..m-149], hasMore=true`**。
4. 用户上滚 → `loadOlderMessages()` → `fetchSessionHistory(A, before=100, 50)` 返回 **`start=50, history=[m-50..m-99]`**。

### 实际 vs 期望（messageId 顺序）
- 步骤 3 后：`currentMessages` 长度 150（正确），但
  - 实际 `historyWindowStarts[A] = 100`，`historyLoadEnd = 100`；**期望 0 / 0**。
- 步骤 4 后：
  - 实际 `len = 200`，`ids = ["m-50"…"m-99", "m-0"…"m-99", "m-100"…"m-149"]`
    → `m-50..m-99` **重复出现**（前插 50 条），unique=150 但序列错误。
  - 期望 `ids = ["m-0"…"m-149"]`（150 条，唯一、有序），且**不应再请求已加载区间**。

### 根因
`refreshCurrentSessionHistory` 无条件把“已加载窗口起点”重写成尾页的绝对起点。此后 `loadOlderMessages` 以 `before=100` 再取 50 条，正是已加载的 `[50,100)`；`mergeHistoryPageByWindow` 的“非重叠更旧页”前置分支 `50+50<=100` 成立，于是整页前插 → 重复 + 窗口起点被再次拉回 50。

### 完整修复算法建议
1. **窗口区间唯一权威**：每 session 维护 `historyWindow = {start, end}`（半开区间），只由页元数据 `data.start + data.history.length` 推导，**禁止**用可见行数或 `data.start` 单值回写。
2. 每次 canonical 合并后：`start = min(oldStart, pageStart)`，`end = max(oldEnd, pageStart + page.length)`；把 `historyLoadEnd = historyWindow.start`。
3. 禁止“更旧页前置”仅凭 `pageStart+len <= previousWindowStart`：必须同时校验该区间与当前窗口**无交集**，有交集时走幂等 union（按绝对位置/稳定 ID 就地更新）。
4. `loadOlderMessages` 请求前先判定 `[before-limit, before)` 是否已被 `historyWindow` 覆盖；覆盖则直接 `hasMore=false`/跳过请求，避免无意义重复合并。
5. `selectSession`（`1098-1099`）同改：A→B→A 不得把窗口起点重置为尾页起点。

---

## 3. F-B · live→result→history 身份重建：工具/思考块错位并重复（P1，确定复现，E）

### 代码位置
- `sessionStore.ts:325-363` `mergeServerHistoryWithLive`：未命中身份的 live 行 `result.push(live)`（`345`）→ **追加到历史尾部**
- `sessionStore.ts:1682-1800` `reconcileWorkerResult`：`1772-1775`
  `const canonical = mergeServerHistoryWithLive(session.history, finalLiveMessages)`
  再由 `mergeFinalMessages` 覆盖 `currentMessages`
- `sessionStore.ts:659-718` `mergeHistoryPageByWindow`：canonical 页刷新时只按显式身份消费 live 行（`688-696`），live 行带 `nativeItemId`、canonical 行带 `messageId` → 互不命中 → 遗留
- 对照 `useWebSocket.ts:819-823` 自身声明：**“Stream arrival order is the display order … never according to the render timing or the current viewport position.”** → 本缺陷与该注释自相矛盾。

### 精确最小事件序列（真实 `useWebSocket` handler，仅 mock ws 单例与 HTTP）
```
worker.status  {sessionId:A, workerId:w1, generation:0, taskSeq:1, status:'running'}
worker.stream  {taskSeq:1, event:{type:'codex.item.completed', item_id:'tool-1',
                                  item:{id:'tool-1', type:'Command', command:'echo hi'}}}
worker.stream  {taskSeq:1, event:{type:'assistant', delta:true, stream_text:'Hello world',
                                  item_id:'item-2', turn_id:'t1',
                                  message:{content:[{type:'text', text:'Hello world'}]}}}
worker.result  {taskSeq:1, status:'done', result:'Hello world'}
fetchSessionHistory(A,0,50) == {history:[u0(messageId m-0), tool('Command({"command":"echo hi"})', m-1),
                                          assistant('Hello world', m-2)], total:3, start:0, hasMore:false}
refreshCurrentSessionHistory()
```

### 实际 vs 期望
| 阶段 | 实际（role:content） | 期望 |
|---|---|---|
| 流式后 | `tool → assistant` | 同（正确） |
| result 后 | `assistant → tool`（+`system:[DONE]…`） | `tool → assistant` |
| 刷新后 | `user:u0, tool, assistant, tool`（工具行**重复**） | `user:u0, tool, assistant`（3 行） |

（store 层等价证据：`evidence/04-probe-raw-output.txt` PROBE 4；管线证据：同文件 PROBE 6 / `evidence/01-investigation-tests.log` E2、E3。）

### 根因
1. `reconcileWorkerResult` 用“先铺 `session.history`，再把未命中身份的 live 行追加到末尾”的方式重建投影；`session.history` 只含本次 result 相关的 assistant 行，**它之前流式到达的 tool/thinking 行不在其中**，于是被追加到 assistant 之后 → 顺序反转。
2. 之后 canonical 页只用稳定 ID（`messageId`）匹配；live 遗留行只有 `nativeItemId`，**两者无交集**，`mergeHistoryPageByWindow` 无法消费它 → 同一工具块同时以 canonical 行和 live 行存在。

### 完整修复算法建议
1. **顺序由“绝对位置/稳定 ID”决定，不由“先 server 后 live”决定**：`reconcileWorkerResult` 不应重建整表；应就地替换（in-place）当前投影中属于本次 task 的行，保留各自既有 index。
2. **建立 `nativeItemId → messageId` 的 turn 级别名映射**：
   - 后端 `worker.result` 已声明 `terminalCoverage: {historyEpoch, historyRevision, messageIds[]}`（`types/index.ts:306-310`，**当前前端完全未消费**）。让 adapter/后端在终态携带本 turn 落盘的 `messageId`/`blockId`，前端把它绑到 live 行的 `nativeItemId`；
   - 或在 canonical 页里为每个 block 同时回传 provider `item_id`/`block_id`（`_api_history` 目前只写 `messageId`，见 `server.py:3551-3600`），使 live 行与 canonical 行可直接对齐。
3. **overlay 生命周期**：一旦某绝对区间的 canonical 页到达，必须**移除**该区间内的 live overlay。具体地，`mergeHistoryPageByWindow` 在身份未命中时，若 canonical 行与 overlay 行**绝对位置一致且 role 相同、内容兼容**，应消费该 overlay（replace）而不是保留两条。
4. 保留 `mergeServerHistoryWithLive` 仅用于“历史分页/快照叠加 live 尾巴”，**不得**在 result 收敛路径上用它重建整表。

### F-B 扩展 · 多块 turn 与连续 DONE 轮（P1，确定复现，E）

**最小事件序列（store 层，R8；与 astra `second-result-loses-earlier-turn-blocks` 逐字一致）**
```
reset: currentMessages=[user 'question'(u)]
applyLiveStream([assistant'analysis'(a), tool'tool'(t), assistant'final'(f)], meta{taskSeq:1})
reconcileWorkerResult({result:'final', status:'done'}, meta{taskSeq:1})
addMessage({role:'system', content:'DONE-1', nativeItemId:'done-1'})
appendDeliveredMessages([{role:'user', content:'question2', queueItemIds:['q2']}])
applyLiveStream([assistant'second analysis'(a2), assistant'second final'(f2)], meta{taskSeq:2})
reconcileWorkerResult({result:'second final'}, meta{taskSeq:2})
```
| 阶段 | 实际 | 期望 |
|---|---|---|
| R7 单轮 result 后 | `[question, final, analysis, tool]` | `[question, analysis, tool, final]` |
| R8 第二轮 result 后 | `[question, final, question2, second final, DONE-1, second analysis]`（6 行） | 8 行，`analysis`/`tool` 仍在原位 |
| E4 真实管线两轮 DONE 后（非 system） | `[assistant:Hello world, assistant:Second answer]` | `[tool, assistant:Hello world, assistant:Second answer]` |

**结论**：`reconcileWorkerResult` 每次都按“`session.history` 优先 + 未命中 live 追加到末尾”重建，`session.history` 只保留与 `result` 文本收敛的那一条 assistant，因此**每个已完成 turn 的非 assistant 块（analysis/tool）会被移动到末尾；第二轮 reconcile 后这些块从可见投影中彻底消失（E4 中 tool 整行丢失）**。这与 `useWebSocket.ts:819-823` 自述的 arrival-order 契约直接冲突，也是本批最严重的可见回归。

**修复**（与 F-B 主修复合并，见 §8 步骤 3）：`reconcileWorkerResult` 只就地替换本 turn 的目标行；不得丢弃/重排其它 turn 的行；`mergeServerHistoryPreservingLocal` 的收敛必须保序且不吞行。

---

## 3b. F-E · `applyLiveStream` 缓存投影下标不校验 role/身份 → 覆盖 user 行（P1，确定复现，E）

### 代码位置
`sessionStore.ts:1637-1657`（`applyLiveStream`）：
- `1639-1644` 第一 fallback：`previous.projectionIndexes[key]` → **有 `projected[index].role === live.role` 校验**（安全）。
- `1645-1651` 第二 fallback：取 `previous.messages[liveOffset]` 的缓存下标，`find` 里**只有 `index >= 0 && index < projected.length`**，**既不校验 role 也不校验身份** → 一旦外部（旧历史前插等）令数组位移，缓存下标就指向另一行并被 `{...projected[targetIndex], ...live}` 就地覆盖。
- `1658-1665`：命中即覆盖；未命中才 push。

### 精确最小事件序列（R9）
```
currentMessages=[user 'question'(u)]
applyLiveStream([assistant 'live'(nativeItemId a)], meta)          → 缓存 projectionIndexes{'assistant:message:a':1,...}
currentMessages = [assistant 'old-history'(messageId old), user 'question', assistant 'live']   // 外部前插，下标整体 +1
applyLiveStream([assistant 'live updated'(nativeItemId a)], meta)  // 第二 fallback 用缓存下标 1
```

### 实际 vs 期望
- 实际：`['assistant:old-history', 'assistant:live updated', 'assistant:live']`
  → **user `question` 行被 assistant 覆盖**，且 `live` 行重复。
- 期望：`['assistant:old-history', 'user:question', 'assistant:live updated']`。

### 完整修复算法建议
1. 第二 fallback 与第一 fallback 用同一套校验：`projected[index].role === live.role` **且**该下标的 `projectionKeys` 与本次 `keys` 有交集（身份一致）才接受。
2. 更稳的做法：投影缓存保存 `{index, identity}` 快照，命中前比对 `projectionKeys(projected[index])` 是否仍包含缓存 identity；不一致即视为失效，走 findIndex/追加。
3. 任何导致 `currentMessages` 被外部整体改写（分页前插、history 合并、delivered 追加）的路径，都应 `invalidateProjectionIndexes(sessionId)`（或把 `projectionIndexes` 存为“绝对窗口下标”而非数组下标）。

---

## 3c. F-F · 无 provider id 的 `${role}:legacy:${content}` 归并（P2，确定复现，E）

### 代码位置
`sessionStore.ts:645-650` `projectionKeys`：无显式身份时退化为 `${message.role}:legacy:${message.content}`；被 `applyLiveStream`（`1653-1656` findIndex）、`reconcileWorkerResult`→`mergeServerHistoryWithLive`（`325-363`）复用。

### 精确序列与实际/期望
- **R10**：已有 id-less `assistant 'same reply'` → `appendDeliveredMessages([user 'new question'])` → `applyLiveStream([id-less assistant 'same reply'])`。
  实际 = 2 行（新回复被并入旧行）；期望 = 3 行 `['same reply','new question','same reply']`。
- **R11**：`[id-less assistant 'interim', tool, assistant 'final']` 流式后 `reconcileWorkerResult('final')`。
  实际 = `['question','final','interim','tool','final']`（5 行，重复）；期望 = `['question','interim','tool','final']`。

### 修复建议
`legacy:` 文本键只允许**一对一、尾行、同 turn 窗口内**的收敛，并且必须带 turn/task 作用域；不得跨 turn 用同文本归并（F-F 与 R6 正控的边界：**有稳定 id 才允许同文本共存，无 id 只能尾行精确匹配一次并记录不确定性**）。

---

## 4. F-C · `historyEpoch` / `historyRevision` 只写不读（P1，确定复现，E）

### 代码位置
- 唯一的三处写入（均为 `data.x ?? old` 回退）：
  - `sessionStore.ts:1079-1080`（selectSession）
  - `sessionStore.ts:1163-1164`（refresh）
  - `sessionStore.ts:1239-1240`（loadOlderMessages）
- 全仓 grep：`historyEpoch|historyRevision` 在 `sessionStore.ts` 里**没有第四处**（无任何比较）。
- 类型已就绪：`types/index.ts:111-116`；响应字段：`types/index.ts:471-479`。
- 后端已提供：`session.py:390-405`（append 递增 revision、replace 换 epoch）、`session.py:642-707`（每页带 epoch/revision）、`server.py:1695-1696 / 1718-1723`。

### 精确最小事件序列
1. 本地会话 A：`historyEpoch='eNew'`、`historyRevision=5`，`currentMessages=[u0, a0]`。
2. `fetchSessionHistory` 返回**旧 scope**：`{history:[u0, 'OLD-a0'], total:2, start:0, historyEpoch:'eOld', historyRevision:1}`。
3. `refreshCurrentSessionHistory()`。

### 实际 vs 期望
- 实际：`session.historyEpoch='eOld'`（**从 eNew 回退**）、`historyRevision=1`（**从 5 回退**）、内容被替换为 `['u0','OLD-a0']`。
- 期望：旧 `historyEpoch`/更低 `historyRevision` 的响应被**拒绝**（保留 `eNew`/`5`/原内容），或作为“scope 变更”显式走整窗重置（替换旧 epoch 的全部已加载行），而不是静默回退。

### 根因
现有守卫（`_selectionSeq` / `_historyRefreshSeq` / `_historyPageSeq`）只能排序**本客户端自己发出的请求**；它们无法察觉服务端在两个请求之间发生了 `replace_history`（换 epoch）。唯一能表达这件事的信号就是页里的 `historyEpoch`/`historyRevision`，而它们没有任何比较逻辑。因此 `branch` / `reimport` / 显式历史替换后的在途旧页可以整页覆盖新历史。

### 第二个面：epoch 替换后旧 tail 残留（R12，astra `history-epoch-replacement-removes-old-tail`）
```
currentMessages=[user 'old user'(old-u), assistant 'old answer'(old-a)]  historyEpoch='hist' rev=10
fetchSessionHistory → {history:[user 'replacement'(new-u)], total:1, start:0, hasMore:false,
                       historyEpoch:'replacement-epoch', historyRevision:20}
refreshCurrentSessionHistory()
```
- 实际：`['user:replacement', 'assistant:old answer']` —— **旧 epoch 的 `old answer` 仍留在尾部**。
- 期望：`['user:replacement']`（epoch 变化 ⇒ 服务端整段替换 ⇒ 旧行必须失效）。
- 机制：`mergeHistoryPageByWindow` 把新页按绝对位置合并进既有数组，页只声明 `[0,1)`，数组其余部分（旧 tail）被原样保留；没有任何“epoch 变化⇒清空”的分支。

因此 F-C 需要**两种语义**同时成立：`epoch 变 → 丢弃旧 scope 的全部已加载行`（R12）与 `epoch 同且 revision 更低 → 拒绝`（R3）。

### 完整修复算法建议
1. 每 session 维护 `acceptedHistoryEpoch` / `acceptedHistoryRevision`（可与 `historyWindowStarts` 同层）。
2. 收到任一 history 响应时：
   - `data.historyEpoch !== acceptedEpoch` → **scope 变更**：丢弃该 session 已加载的全部 canonical 行与窗口区间，以本页为新 epoch 起点建立窗口；同时清该 session 的 live overlay / 分页游标。
   - `epoch 相同 && data.historyRevision < acceptedRevision` → **拒绝**该响应（不写 store、不动窗口）。
   - 其余 → 正常合并，并 `acceptedRevision = max(acceptedRevision, data.historyRevision)`。
3. 由于分页页是子区间（revision 相同），不要用 `revision >` 作为“接受”的必要条件，只用它做**陈旧拒绝**。
4. 过渡期兼容：`historyEpoch` 缺失（旧服务）时退化为现有行为（并置 `acceptedEpoch=null`）。

---

## 5. 队列 / 同文本 / Steer 现状（本轮验证通过，含反例排除）

对应任务清单里的 “queue ACK/tombstone/编辑/失败恢复”“历史同文本合法重复”。这些是**正控**（PASS），下一位 TA 不必重新推导，也**不要**为它们写“修复”。

- **Q1** 迟到 enqueue ACK（rev1）晚于 delivery（rev2）→ 队列不复活、revision 不回退、投递用户行只入一遍。
  实现：`queueStore.ts:88-121` `setSnapshot` 版本门禁 + `107-110` tombstone/delivered 过滤 + `295-340` enqueue 末端 `canonicalQueueId` 复查；`sessionStore.ts:2024-2134` `appendDeliveredMessages` 用 `_deliveredQueueIds` 去重。
- **Q2** 迟到 PATCH ACK 不能改写已投递行（`queueStore.ts:394-419` 仅在 `accepted` 后调 `updateQueuedMessage`；`sessionStore.ts:1944-1982` 只在 `_pendingQueueIds` 命中时改）。
- **Q3** 删除失败：不落 tombstone、保留条目、toast 报错（`queueStore.ts:486-509`）。
- **Q4** tombstone 阻止后续 `queue.item_added` 复活（`queueStore.ts:105-110, 219-233`）。
- **Q5** 重排乐观 → 服务端快照收敛（`queueStore.ts:511-536`）。
- **Q6** 同 revision 不同形状快照幂等忽略（`queueStore.ts:100-104`）。
- **R6** 历史里两条同文本 user（不同 `messageId`）+ 一条同文本 queued 乐观行 → 三条共存，**不按文本去重**（`sessionStore.ts:169-208` 身份函数；`474-…` 只在“无显式身份 + total 已前进 + 同 ordinal”时收敛）。
  > 依据：`server.py:3551-3600` 的 `legacy:{sid}:{epoch}:{absolute_index}` 兜底 ID 使同文本不同位置天然可分；且 `parts` 参与 `sameMessage` 比较。
  > **边界**：R6 只保证“有稳定身份”的同文本共存。**无任何 id** 时 `projectionKeys` 退回 `${role}:legacy:${content}`，同文本仍会误并 → 见 F-F（R10/R11）。

**Steer（F10）现状（静态，未做组件级实跑）**：
- `InputRow.tsx:1144-1178` 已捕获 `draftRevision` + `draftText` + 附件指纹，`await` 后仅当草稿仍归本事务才清空（`1162-1172`）；`api.ts:726-733` / `717-724` 保留 `if (data.error) throw`。→ 旧报告的“迟到成功抹掉新草稿 / 200+error 假成功”两点在代码层已闭合。
- **未验证**：并发多次 Steer 的幂等、未知提交结果的回执查询、真实 provider 行为。未列入本轮结论。

---

## 6. F-D · 同根因的“页序反转”（P2，静态推断，S，未单独断言）

`refreshCurrentSessionHistory` 的 `previousWindowStart` 回退 `Math.max(0, data.total - previousHistory.length)`（`1138-1141`）在 `historyWindowStarts[sid]` 未定义且本地投影比服务端窗口短时会给出一个**接近尾部**的值。此时一个从 0 开始的页会被 `mergeHistoryPageByWindow` 当作“更旧页”，走 `splice(max(0, absolute - previousWindowStart), 0, incoming)`（`714`）在索引 0 处反复插入 → **整页顺序反转**。

原始观察（已删除的临时探针，输出见 `evidence/04-probe-raw-output.txt` PROBE 6）：
```
canonical page [u0, tool, assistant]  →  实际 current [assistant, tool, u0, assistant, tool, system]
```

**为何只算静态推断**：该分支需要 `historyWindowStarts[sid] === undefined`。正常路径下 `selectSession` 会在进入会话时写 `historyWindowStarts[id]`（`1016-1019`），因此“已选中会话”很难命中 undefined；`acceptServerEpoch` 也不清它。故未把它做成断言用例，避免制造不可达的“假缺陷”。它与 F-A 同根因（窗口元数据不可靠），修 F-A 时一并覆盖。

---

## 7. 与旧报告 / 931f1cc 的差异

| 旧报告（`99f5c88`）发现 | `931f1cc` 后状态 | 本轮结论 |
|---|---|---|
| F01 tail refresh 把历史分页当 live，`inferDivergedLiveMessages` 丢旧 user | 函数已删除，改为 `mergeHistoryPageByWindow`（绝对窗口） | 主症状消失，但**新引入**窗口起点回写缺陷（F-A） |
| F02 全历史文本前缀匹配吞消息 | `mergeServerHistoryWithLive` 已改为身份优先；`reconcileWorkerResult` 只认身份或“尾行精确文本” | 带稳定 ID 的路径已闭合（R6 正控）；**无 ID 路径仍按 `${role}:legacy:${content}` 归并** → F-F |
| F07 queue 写响应可回退 revision / 复活已投递项 | `setSnapshot` 加版本门禁 + tombstone/delivered 过滤 | **已修**，Q1–Q6 通过 |
| F10 Steer 迟到成功抹草稿 / 200+error | 加 draftRevision 事务 + 保留 error 检查 | **已修**（静态）|
| F05 eventSeq 无幂等消费 | `ws.ts` 加 delivery/source 双游标 + snapshot resync | 有覆盖（`ws.stage3.test.ts`），本轮未发现新问题 |
| F06 三入口请求保护不协调 | 加 `_selectionSeq/_historyRefreshSeq/_historyPageSeq` | 请求排序已具备；但**跨 epoch** 无法察觉 → F-C |
| （新） | — | **F-A / F-B / F-C / F-E / F-F / F-D** |
| （新，首轮未覆盖） | — | F-E：投影缓存下标越界覆盖（本轮 #2）；F-B 扩展：连续 DONE 轮丢块 |

---

## 8. 给下一位 TA 的精确执行方案

> 全部只动 `packages/web/src/`（React 源码）。改后 `cd packages/web && pnpm build`；`python -m pytest tests/ -q` 不在本任务范围（未跑）。

### 步骤 0 · 复现（先确认失败，再动手）
```bash
cd packages/web
pnpm install --frozen-lockfile --offline            # EXIT 0
pnpm exec vitest run src/stores/reaudit.history.test.ts \
                    src/stores/reaudit.queue.test.ts \
                    src/hooks/reaudit.orderedEvents.test.tsx --reporter=verbose   # EXIT 1, 14 failed / 8 passed
```
失败用例即验收目标；`R6/E1/Q1–Q6` 是**回归护栏**，修复后必须仍 PASS。

### 步骤 1 · F-A（最先做，改动最小、收益最大）
- 在 store 增加 `historyWindows: Record<string, {start:number; end:number}>`。
- `selectSession`（`1016-1019, 1098-1099`）、`refreshCurrentSessionHistory`（`1181-1182`）、`loadOlderMessages`（`1249-1250`）：把 `historyWindowStarts[sid] = data.start` 改为区间 min/max 合并；`historyLoadEnd = 区间 start`。
- `loadOlderMessages` 入口（`1190-1203`）增加“目标区间已被覆盖则跳过”。
- `mergeHistoryPageByWindow`（`678`）前置分支改为“有交集→union、无交集且严格更旧→prepend”。
- 验收：R1、R2 由 fail→pass；R6、E1、Q1–Q6 保持 pass。

### 步骤 2 · F-C（独立，可与步骤 3/4 并行）
- 加 `acceptedHistoryEpoch/_Revision`；三处写入改成“先 gate 再写”。
- `epoch 变化 ⇒ 丢弃该 session 已加载行与窗口区间`（R12）；`epoch 同 && revision 更低 ⇒ 拒绝`（R3）。
- 验收：R3、R12 pass。

### 步骤 3 · F-B（改动最大，建议单独小 PR；**连续 DONE 轮是首要验收**）
- `reconcileWorkerResult`（`1682-1800`）改为**就地替换本 turn 目标行**，不得重建整表、不得丢弃/重排其它 turn 的行。
- 为 live↔canonical 建立稳定别名：优先消费 `worker.result.terminalCoverage.messageIds`（`types/index.ts:306-310`，需后端在终态填充 + `_api_history` 回传 provider `item_id/block_id`）；过渡期在 `mergeHistoryPageByWindow` 增加“绝对位置 + role + 内容兼容即消费 overlay”。
- `mergeServerHistoryPreservingLocal`（`474-…`）收敛必须保序且不吞行。
- 验收：R4、R5、**R7、R8**、E2、E3、**E4** 由 fail→pass；E1 保持 pass。

### 步骤 3b · F-E（与步骤 3 同区，1 处小改）
- `sessionStore.ts:1645-1651` 第二 fallback 补 `projected[index]?.role === live.role` **且** 身份交集校验（或改用 `{index, identity}` 快照 + 失效机制）。
- 任何外部整体改写 `currentMessages` 的路径（分页前插 / history 合并 / delivered）后需失效 `projectionIndexes`。
- 验收：R9 pass；R6/E1 保持 pass。

### 步骤 3c · F-F（与步骤 3 同区）
- `legacy:` 文本键限定为“同 turn、尾行、一对一”，并带 `taskSeq/turnId` 作用域；不得跨 turn 归并同文本。
- 验收：R10、R11 pass；R6（有稳定 ID 的同文本共存）保持 pass。

### 步骤 4 · 与 F-A 一并覆盖 F-D
- 删掉 `1138-1141` 的 `data.total - previousHistory.length` 回退，一律使用窗口区间元数据。

### 步骤 5 · 交付前必须
- 全量 `pnpm exec vitest run` 必须回到 `X failed = 0`（当前基线 586 tests 全过；修好后应为 71 files / 608 tests 全过）。
- 保留本报告与 `evidence/`；不要为通过测试而改测试断言或放宽生产逻辑。
- 补后端联动测试：真实 `append_history`/`replace_history` 后 `history_page` 的 epoch/revision 与前端 gate 的契约（本轮**未**跑 pytest，属未验证项）。

---

## 9. 证据文件清单

| 文件 | 内容 |
|---|---|
| `evidence/00-baseline-no-new-tests.log` | 基线全量（68 files / 586 tests，vitest exit 0，新测试文件移出后运行） |
| `evidence/01-investigation-tests.log` | 调查测试 v1 逐条 verbose（7 failed / 8 passed） |
| `evidence/02-full-suite.log` | v1 全量 dot（71 files / 601 tests） |
| `evidence/03-code-anchors.txt` | 全部引用的代码行号（已 grep 复核） |
| `evidence/04-probe-raw-output.txt` | 6 个临时探针的原始 console 输出（其中 3 个探针文件已删除） |
| `evidence/05-git-state.txt` | HEAD / toplevel / `git status`（证明仅新增 untracked 文件） |
| `evidence/06-reproducibility.txt` | 调查测试重复 2 次运行，均 `7 failed / 8 passed`（vitest exit 1） |
| `evidence/07-investigation-tests-v2.log` | **v2 逐条 verbose（14 failed / 8 passed，含 R7–R12/E4 observed 值）** |
| `evidence/08-full-suite-v2.log` | **v2 全量 dot（71 files / 608 tests，14 failed）** |
| `AUDIT_HISTORY_QUEUE.md` | 本报告 |

### 外部（只读引用，未修改）的独立证据
- `D:/project/pan-worktrees/frontend-reaudit-astra-20260921/audit/probe-current-main.cjs`（真实 Zustand 探针，HEAD=`591367a`）
- `D:/project/pan-worktrees/frontend-reaudit-astra-20260921/audit/evidence/current-main-probes.json`（9 个快照；本报告 R7–R12 的 observed 与其一致）

> 注意：`evidence/*.log` 命中了仓库 `.gitignore:77` 的 `*.log` 规则，因此不会出现在 `git status` 里，但**已全部落盘**于本 worktree。若需随分支交付，请改为 `.txt` 后缀或 `git add -f`。


**最终边界声明**：本报告证明的是**源码级、确定性、可复现**的前端 reducer/管线缺陷（E 级）。它**不**证明 practical 现场已复现、不证明服务端已损坏持久化、不证明冷重启/真实 provider/真断线路径。产品代码未改，无提交、无合并、无部署、未接触 8768。
