# Pan 消息协议与身份字段表（R0 侦察，基线 591367a）

只用真实源码/真实 fixture 得出；未在实测中确认的字段标注为「未确认」。

## 1. 服务端身份与游标

| 字段 | 来源（真实代码） | 语义 | 形状 / 备注 |
|---|---|---|---|
| `Session.history_epoch` | `packages/core/session.py` | 历史身份作用域；**仅在 `replace_history()`（显式全量历史边界）时换成新 uuid**，普通 append 不变 | `uuid4().hex` 或旧数据 `legacy:<id>` |
| `Session.history_revision` | `session.py:append_history/replace_history` | 同一 epoch 内单调递增的规范历史版本 | int，每次 append/replace +1 |
| `session.task_seq` | `packages/core/worker.py` | 跨 worker respawn 单调的**任务**序号；`taskSeq` 统一取 `w._current_seq` | int |
| `w._current_task_id` | `worker.py` | 当前持久任务身份；随 `worker.stream`/`worker.result` 下发 | str \| None |
| `w.generation` | `worker.py` | 运行期 generation，用于丢弃迟到的生命周期事件 | int |
| `historyTotal` / `total` | `session.py:history_page` | 存储层绝对行数（= 行总数） | int |
| `start` / `hasMore` | `history_page` | 分页**绝对**起点 / 是否还有更早页 | int / bool |
| `result_cursor` / `terminalKey` | `worker.py:_persist_terminal_state` | 终态可重放游标 / 幂等键 | int / str |

### `GET /api/sessions/{id}/history`（`server.py:4649`）
返回 `{history, total, hasMore, start, historyEpoch, historyRevision}`；`history` 经 `_api_history()` 序列化。

### `_api_history()` 的 **messageId 是合成值**（关键）
`server.py:3551`：`message.get("messageId")` 不存在时合成
`legacy:{session_id}:{history_epoch or 'legacy'}:{absolute_index}`。
→ **CBC/Codex 落盘行本身没有 messageId**，wire 上的 `messageId` 完全由「session + epoch + 绝对下标」派生。
→ 只要 epoch 不变且历史 append-only，该 messageId 稳定且可作身份；epoch 一变，全部 ID 失效（必须整体替换）。

## 2. 真实 provider 形状（实测源码，非补造）

### CBC（`packages/core/adapters/cbc/adapter.py:375 extract_assistant_blocks`）
```
{"role": "assistant"|"thinking"|"tool", "content": str}
```
**没有任何 id**：无 `messageId` / `blockId` / `nativeItemId` / `item_id`。
tool 内容 = `f"{name}({json.dumps(input, separators=(',',':'))})"`。
→ 这就是「真实 CBC 无 item_id 形状」：**身份只能由 epoch+绝对偏移（持久层）或任务内槽位（live 层）提供**，禁止按正文做身份。

### Codex（`packages/core/adapters/codex/adapter.py:417`）
- `type == "assistant"` 分支：`native_item_id = event.get("item_id") or event.get("itemId")`，**只加在 `tool_use` block 上**；text/thinking block 不带 id。
- 非 assistant 事件走 `event["item"]`：`agentMessage`→`{role:assistant, content:text}`（无 id）、`reasoning`→thinking、tool item→tool。
→ 因此 assistant 文本块**同样常无 id**，不能假设有 `item_id`。

## 3. live / terminal 事件契约

### `worker.stream`（`worker.py:1580`）
`{type, workerId, sessionId, generation, taskSeq, taskId, event}`，`event` 为原始 provider 事件。
前端 `useWebSocket.appendEventToMessages()` 把 `event` 投影成 `Message[]`，**每次把整段累积 buffer** 交给 `applyLiveStream`（见 `useWebSocket.ts:989-995`）。→ `applyLiveStream(sid, messages, meta)` 的 `messages` = **该任务当前全量 live 列表**（非单条 delta）。
`stream_text` 为**累计**文本（cumulative）；`delta` 帧用 `event.delta`。

### 终态顺序（`worker.py:_persist_terminal_state` → `_publish_terminal_events`）
1. **先持久化**：`append_history(s, {"role":"assistant","content":result})`（若末行不是完全相同的 assistant）→ `history_revision` 前进；`last_result/terminal_results` 写入 `historyEpoch/historyRevision` 覆盖（terminal coverage）。
2. `await _flush_history_now(w)` ← durable base commit。
3. 再广播 **`worker.result`**：`{status, result, taskSeq, taskId, generation, resultCursor, terminalKey, historyEpoch, historyRevision, terminalCoverage:{historyEpoch, historyRevision}}`。
4. 再广播 **`worker.status {status:"idle"}`**。

→ 契约：**durable history flush → result → idle**。前端可在收到 result 后安全做权威 history recovery（coverage 已可用）。
→ `terminalCoverage` **现状前端完全未消费**（`grep terminalCoverage src/` 无命中）——是本次要接的字段。

## 4. 前端本地身份（现状）

| 字段 | 说明 |
|---|---|
| `Message.messageId` | wire 规范身份（服务端合成 legacy 值） |
| `Message.blockId` | 复合消息的 provider block 身份（当前 provider 都不下发） |
| `Message.nativeItemId` | Codex/旧适配器的瞬时 native 身份；**CBC 无** |
| `Message.queueItemIds` | 本地排队投递身份（显式，可靠） |
| `withLocalUserIdentity()` | 前端给本地 user 行补 `nativeItemId = local:user:{sid}:{n}` |
| `liveStreamBuffers[sid].projectionIndexes` | 缓存 array index（`projectionKeys()` 派生） |
| `projectionKeys()` | 有身份用 `role:id`；**无身份退化为 `role:legacy:{content}`** ← 跨任务同文本碰撞根因 |

### 已确认的缺陷根因（读码 + 探针双证）
1. `reconcileWorkerResult` 先把 result 作为**新 assistant 追加到 history 尾部**，再 `mergeServerHistoryWithLive(history, live)` 把 live 块**追加其后** → 顺序变 `user, final, analysis, tool`。
2. 该路径下上一轮的 analysis/tool **从未进入 `history`**，下一轮以 lossy history 为基重建 → 旧块丢失；DONE（仅存于 `currentMessages`，不在 history）被按「非 system 计数」重插 → 漂到第二轮之后。
3. `projectionKeys` 无身份时按 `role:content` 作键 → 跨任务同文本被当同一消息吞掉。
4. `applyLiveStream` 复用缓存的 array index，仅校验 `role` → prepend 后索引 1 仍写索引 1，assistant 覆盖 user。
5. `refreshCurrentSessionHistory` 无条件 `historyWindowStarts[sid] = data.start`、`historyLoadEnd = data.start` → 尾页刷新把已加载最早 offset 从 0 改成 4；`mergeHistoryPageByWindow` 对重叠加页按相对位置重排。
6. `historyRevision` 无单调校验：`data.historyRevision ?? session.historyRevision` 直接接受更旧 revision。
7. 换 epoch 时只覆盖 `historyEpoch` 字段，不清理旧 canonical 行 → 旧尾巴残留。

## 5. 未确认 / 待实测
- CBC worker 真实运行时的 `taskSeq` 是否首个任务即为 1（探针用 1/2；E2E 会用真实事件核对）。
- `worker.stream` 的真实 `event.delta`/`stream_text` 组合（E2E 抓真实帧确认）。
- 多客户端并发写同一 session 时的 anchor 精度（本次 E2E 单客户端，未覆盖；报告为限制）。
