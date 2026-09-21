# AUDIT_PROTOCOL_PROVIDER.md

**跨层 provider → worker → server WS → frontend 契约再审查**

- 基线（固定）：`591367a65f88e9d5270e8e99920ef5448d1d68c9`（`fix: unify Codex tool live and history projection`）
- 隔离 worktree：`D:/project/pan-worktrees/frontend-reaudit-protocol-ds-20260921`（detached HEAD，`git-common-dir = D:/project/Pan/.git`），开始时工作区干净
- 本任务**未修改任何产品代码**，未 commit / merge / push，未访问 8768，未触碰 `practical/`、`Pan-main`，未接管其他 TA 任务
- 新增产物：本文件、`evidence/`（探针脚本 + 原始输出 + 测试日志）、`tests/test_reaudit_protocol_provider.py`
- 未调用任何真实模型或真实 CLI；全部结论来自**真实 adapter / worker / server / store 代码**在确定性 fixture 下的执行，或明确标注的静态推断

---

## 0. 证据等级约定

| 标记 | 含义 |
|---|---|
| **【确定复现】** | 用真实生产代码执行，原始输入/输出已落盘 `evidence/` |
| **【静态推断】** | 源码可确定，但未在真实进程/浏览器中触发 |
| **【未验证】** | 需要真实 provider 行为或真实浏览器才能判定，本任务未取得证据 |

**已验证为正确（不要再去追）的项集中在第 3 节**——包括上一轮报告中的若干怀疑点（WS 游标、合法合并、epoch 冷重启、Codex 工具序列化）。

---

## 1. 契约地图（当前 main 的精确锚点）

| 层 | 锚点 | 关键契约 |
|---|---|---|
| provider(live) | `adapters/codex/app_server_wrapper.py:130 _item_event` / `:684 item/completed` / `:706 turn/completed` | completed item → `{type:"assistant", message:{content:[...]}, final:true, item_id, turn_id}`；`turn/completed` 只置 `state["done"]` 与 `last_text`，随后由 `run_turn` 发 `result` |
| provider(live, exec 兜底) | `adapters/codex/wrapper.py:114 _forward_and_collect` | 原样转发 `item.completed` 的 raw `item`，读完 stdout **之后**才发 `result` |
| provider(canonical) | `adapters/codex/sessions.py:223 _item_to_block`、`adapters/codex/tool_projection.py` | `canonical_tool_content` = `json.dumps(ensure_ascii=True, separators=(",",":"), default=str)`；附 `nativeItemId` |
| worker 事件→history | `worker.py:1527 is_assistant_event → extract_assistant_blocks → append_history`；`:1536 is_result_event` | live 块先落 history，`result` 再走 terminal |
| worker terminal | `worker.py:860 _persist_terminal_state`、`:1027 _publish_terminal_events` | 先持久化 `terminal_results/last_result` + flush history，再广播 `worker.result` → `worker.status(idle)`；`terminalCoverage.historyRevision` 声明 result 覆盖到哪一版 |
| worker result 追加 | `worker.py:972-976` | 仅当**最后一行**不是同文本 assistant 时，把 `result` 作为新 assistant 行追加 |
| session 持久化 | `session.py:390 append_history`（`history_revision += 1`）、`:399 replace_history`（换 `history_epoch`）、`:1564 _save_body`（`_hist_persisted` 增量游标） | 进程内 history 为权威，JSONL 为镜像 |
| server WS | `web/server.py:1176 _stamp_live_event`（`eventSeq` 全局自增）、`:819 _OutboundClient`（`deliverySeq` 每连接）、`:695 _merge_stream_deltas`（合并 delta）、`:1376 _resync_snapshot` | 源游标全局、投递游标每连接；快照为权威边界 |
| frontend 传输 | `web/src/services/ws.ts:51-124` | delivery cursor 连续检查 + source cursor 范围检查 |
| frontend reducer | `web/src/hooks/useWebSocket.ts:796 appendEventToMessages`、`:970 appendEvent` | `final`/`replace`/别名/累计文本 |
| frontend terminal | `web/src/stores/sessionStore.ts:1682 reconcileWorkerResult`、`:1608 canApplyLiveStream`、`:1481 acceptServerEpoch` | terminal watermark 拒绝旧帧；epoch 变化清 runtime 状态 |
| wire 身份 | `web/server.py:3551 _api_history` | 无 `messageId` 时身份 = `legacy:{sid}:{epoch}:{absoluteIndex}` |

---

## 2. 发现

严重性：**P1** = 用户可见的消息内容/条数错误或持久化事实与广播声明不一致；**P2** = 特定竞态/降载下的丢失或身份不稳定。

### F1 · P1【确定复现】terminal result 在“最后一块是工具”时重复追加最终答案

**代码**：`worker.py:972-976`。判定只比对 `s.history[-1]`：

```python
if isinstance(result_text, str) and result_text.strip():
    last = s.history[-1] if s.history else None
    if not (last and last.get("role") == "assistant" and last.get("content") == result_text):
        _sess.append_history(s, {"role": "assistant", "content": result_text})
```

Codex 一个 turn 的正常形状是 `agentMessage → tool → …`，最后一块经常是工具块（`item/completed` 的 `commandExecution`/`mcpToolCall`/`fileChange` 也映射成 `type:"assistant"` 的 tool_use 块）。此时 `s.history[-1]` 是 tool 行 → 条件为真 → **把仍是最后一次 assistant 正文的 `result` 又追加一遍**。

**证据（已落盘）**：`evidence/probe_protocol_provider.out.json` → `P2_worker_terminal.b_tool_last`
输入 `[assistant "A", tool Command({"command":"ls"}), result "A"]`，得到

```
history = [ {assistant "A"}, {tool Command(...)}, {assistant "A"} ]   history_revision = 3
```

**真实持久化复证**（`evidence/probe_hist_persisted.out.json` → `tool_last`）：`<sid>.history.jsonl` 实际写入 3 行 `[A, Command(...), A]`，`_hist_persisted=3`，reload 后仍是同样 3 行 → **重复的最终答案能跨进程重启存活**。

**回归测试**：`tests/test_reaudit_protocol_provider.py::test_terminal_result_is_not_appended_when_a_trailing_tool_covers_it`（`xfail(strict=True)`）；`--runxfail` 原始失败：

```
AssertionError: assert ['A', 'A'] == ['A']
evidence/pytest_reaudit_runxfail.txt   (exit 1)
```

**精确修复**：在 `_persist_terminal_state` 中把“最后一行去重”换成“本轮覆盖检查”。
1. 在 `_read_stdout` 里，当本轮第一条 assistant 块被 append 前记录 `w._turn_history_start = len(s.history)`（用 `_current_seq`/`_current_task_id` 变化作为轮次边界）。
2. result 判定改为：在 `s.history[w._turn_history_start:]` 中查找 `role=="assistant" and content==result_text` 且 `nativeItemId` 与本轮 live 末尾一致的行；
   - 命中 → 不追加（可顺带把该行的 `nativeItemId` 补上）；
   - 未命中但本轮最后一行是 `result_text` 的**真前缀** assistant → 原地升级为 `result_text`（不新增行）；
   - 其他情况才追加。
3. `w._turn_history_start` 在 `_finish_terminal_bookkeeping` 或轮次结束时清空。

**验收断言**（必须同时查顺序与内容，不能只查条数）：

```python
assert [r for r in history if r["role"] == "assistant"] == [{"role": "assistant", "content": "A"}]
assert [r["role"] for r in history] == ["assistant", "tool"]      # 保持出现顺序
assert last_result["result"] == "A" and terminal["historyRevision"] == session.history_revision
```

---

### F2 · P1【确定复现】聚合型 result 在逐 item 行之外再造一条 assistant 行；前端随后产生两条相同行并丢失中间块

**代码**：`worker.py:972-976`（同 F1 根因）+ `sessionStore.ts:1682 reconcileWorkerResult`。
worker 侧：`result` 若等于若干 assistant 行的拼接（聚合语义），最后一行不是它 → 追加第 3 行。
前端侧：`reconcileWorkerResult` 以 `liveAssistant = 最后一个 assistant live 行` 为唯一目标，把它的 content **整体替换**为 `result`，同时对 `session.history` 按 `liveIds` 找同身份行替换；worker 已写入的聚合行因无 `nativeItemId` 既不被替换也不被删除 → 残留。

**证据**：

1. `evidence/probe_protocol_provider.out.json` → `P2_worker_terminal.d_aggregate_result`
   `[assistant "first half ", assistant "second half", result "first half second half"]` →
   `history = ["first half ", "second half", "first half second half"]`（第 3 行是新增重复）。
2. `evidence/probe_frontend_reconcile.out.json` → `aggregate_result_vs_two_canonical_rows`
   真实 `useSessionStore.reconcileWorkerResult` 输出：

```
sessionHistory   = [user q, assistant "first half ", assistant "first half second half", assistant "first half second half"]
historyTotal     = 4
currentMessages  = 同上（两条相同聚合行）
```

→ 用户看到**两条一模一样的最终消息**，且 `"second half"` 块被覆盖丢失。

**回归测试**：`test_aggregate_result_does_not_add_a_duplicate_assistant_row`（`xfail(strict=True)`），`--runxfail` 原始失败：

```
AssertionError: assert ['first half ... second half'] == ['first half ', 'second half']
```

**精确修复**：
- worker：按 F1 的覆盖检查扩展为“段落覆盖”——若 `result_text` 等于本轮 assistant 行序列的拼接、后缀拼接或末行，则不新增；否则追加一条并**在同一逻辑上标注它覆盖了哪些行**（新增 `coversHistoryRange`，随 `worker.result` 发出）。
- 前端 `reconcileWorkerResult`：
  1. 若 `result` 等于本轮 live assistant 行的拼接 → 不替换、不新增（逐行保留）。
  2. 只有当 `session.history` 末尾（同一 taskSeq 覆盖区间内）不存在同文本 assistant 行时才允许 push；push 前用 `terminalKey`/`taskSeq` 作为幂等键。
  3. 替换时按 `nativeItemId`/`messageId` 精确定位，禁止用“最后一个 assistant”兜底覆盖不同 item。

**验收断言**：

```python
assert [r["content"] for r in history if r["role"] == "assistant"] == ["first half ", "second half"]
assert currentMessages == canonical_history_rows
assert historyTotal == len(history) and len([r for r in currentMessages if r["content"] == "first half second half"]) == 0
```

---

### F3 · P1【确定复现】无 live assistant 时前端把同一个 result 显示两次

**代码**：`sessionStore.ts:1704-1710`（`result.trim() && !liveAssistant` → 往 `finalLiveMessages` push 一条）与 `:1748-1757`（`replaced=false` → 往 `session.history` push 一条），随后 `canonical = mergeServerHistoryWithLive(session.history, finalLiveMessages)` 把两者合并。

**证据**：`evidence/probe_frontend_reconcile.out.json` → `result_without_live_assistant`

```
input:  history=[user q] + live 空 + result="answer"
sessionHistory  = [user q, assistant "answer"]     historyTotal = 2
currentMessages = [user q, assistant "answer", assistant "answer"]   ← 重复
```

**精确修复**：二者只允许发生一处。推荐：**canonical 行只由 history 提供**；`finalLiveMessages` 仅在 result 尚未落到 `session.history`（即 `historyRevision < terminal.historyRevision`）时才补一条，并用本次 terminal 的幂等键去重。

**验收断言**：无 live assistant 时 `currentMessages` 中该 result 恰出现 1 次；`historyTotal` 与可见行数一致。

---

### F4 · P1【确定复现】terminal 之后仍到达的 assistant 块：既不拒绝也不重算覆盖，`terminalCoverage.historyRevision` 变成过期值

**代码**：`worker.py:1527-1533`（assistant 分支不检查 `_terminal_handled`）、`:991 _ack_current_task` / `:1076 _finish_terminal_bookkeeping`（清 `_current_task_id` 但不停止后续 assistant 追加）。

**证据**：`evidence/probe_protocol_provider.out.json` → `P2_worker_terminal.c_result_then_assistant`

```
输入: [assistant "partial", result "final answer", assistant "final answer"]
history                 = [partial, "final answer", "final answer"]     history_revision = 3
session.last_result.historyRevision = 2      ← 与真实 3 不一致
worker.result.historyRevision       = 2
第二次 worker.stream 帧:  taskSeq=9, taskId=null   ← 同一 task 的晚到流事件丢失 taskId
```

`worker.result` 声明的 `terminalCoverage` 因此**小于**实际 canonical history；同时晚到帧的 `taskId=null` 使前端只能靠 taskSeq 判定为旧帧（`isBlockedByTerminal` 命中，见 `sessionStore.ts:446-451`），语义上“同 task 但丢 taskId”是一个不应出现的形状。

**真实持久化复证**（`evidence/probe_hist_persisted.out.json` → `late_after_result`）：JSONL 实际写入 `[partial, final answer, final answer]`，`_hist_persisted=3`，`history_revision=3`，而 `worker.result.historyRevision=2`；reload 后依旧是 3 行 → coverage 过期是**可持久复现**的事实。

**回归测试**：`test_terminal_coverage_revision_covers_a_late_assistant_event` 与落盘版 `test_terminal_coverage_matches_persisted_rows`（均 `xfail(strict=True)`），原始失败 `assert 2 == 3`（见 `evidence/pytest_reaudit_runxfail.txt`）。

**精确修复**（二选一，必须与 wrapper 顺序契约一致）：
- 首选：`_read_stdout` 在 `w._terminal_handled` 且 `event` 属于已终结的 `taskSeq` 时**丢弃并记日志**（对齐前端 terminal guard）；这要求 provider 保证所有 item 事件先于 `result`（Codex 两条路径都已满足：app-server 在 `run_turn` 内、exec 在 EOF 后才发 result）。
- 若确实需要保留晚到块：必须重算并**再次广播**一个带新 `resultCursor`/`historyRevision` 的 terminal（或显式的 `history.appended` 事件），禁止静默追加；并且晚到帧必须继续携带 `taskId`。

**验收断言**：

```python
assert terminal_event["historyRevision"] == session.history_revision   # 同一 turn
assert all(f["taskId"] for f in late_stream_frames if f["taskSeq"] == terminal_taskseq)
```

---

### F5 · P1【确定复现】CBC live 与 canonical history 的投影**不一致**（本基线只统一了 Codex）

**代码**：`adapters/cbc/adapter.py:375 extract_assistant_blocks`（live） vs `adapters/cbc/sessions.py:685 _event_to_block`（canonical，import/preview 路径）。
`_event_to_block` 的 `function_call` 分支注释声称 “Same format as adapter.extract_assistant_blocks tool_use”，但实现是 `json.dumps(args_raw, ensure_ascii=False)[:500]` —— **空格分隔、非 ASCII 不转义、截断 500 字符**，与 live 的紧凑转义 JSON 完全不同。

**证据**：`evidence/probe_protocol_provider.out.json` → `P6_cbc_live_vs_history`

| 场景 | live | history | 一致 |
|---|---|---|---|
| `{"path":"a.txt","limit":10}` | `Read({"path":"a.txt","limit":10})` | `Read({"path": "a.txt", "limit": 10})` | 否 |
| `{"content":"中文😀"}` | `Write({"content":"\u4e2d\u6587\ud83d\ude00"})` | `Write({"content": "中文😀"})` | 否 |
| 两个 text 块 | 2 行（`" line1\n"`, `"line2 "`） | 1 行 join+strip（`"line1\nline2"`） | 否 |
| 长参数（600 字符） | 618 字符 | 507 字符（截断） | 否 |
| reasoning | `thinking "hmm"` | `thinking "hmm"` | 是（仅因 fixture 对齐） |

**回归测试**：`test_cbc_tool_projection_matches_between_live_and_history`、`test_cbc_multi_text_block_projection_is_stable`（均 `xfail(strict=True)`，原始失败见 `evidence/pytest_reaudit_runxfail.txt`）。

**精确修复**：新增 `adapters/cbc/tool_projection.py`（镜像 codex 的 `tool_projection.py`），`canonical_tool_content(name, args)` 用 `json.dumps(..., ensure_ascii=True, separators=(",",":"), default=str)`；live 与 `_event_to_block` 都调用它；500 字符截断若需要则**两侧同规则**（显式常量 + 相同省略号），不要只截一侧。

**验收断言**：对 `{ASCII 嵌套 / 非 ASCII / 长参数}` 三组工具，`live_blocks == [canonical_block]`；多 text 块：两侧块数与每块正文完全一致。

---

### F6 · P1【确定复现】Codex 同一 turn 内两个不同 native item，若第二个正文以第一个为前缀，前端会合并成一行并保留第一个 item 的身份

**代码**：`useWebSocket.ts:790 nativeTurnItemAliases`、`:839-849`（`nativeIds` 把 `aliasedItemId` 也纳入候选）、`:916-932`（`aliasOnlyMatch` 时仍允许 `b.content.startsWith(target.content)` 走替换分支）。
一个 Pan task（一个 turn）内多个 `agentMessage` 是 Codex 的正常形状；`aliasKey` 用 `${sessionId}{scopeSuffix}:${turnId}`，同一 turn 内所有 item 共享同一 aliasKey。

**证据**：`evidence/probe_frontend_ws.out.json` → `reducer`

```
b6_same_turn_prefix_items  = [["assistant","Hello world","x1"]]        ← 应有两行，x2 被吞、身份仍是 x1
b7_same_turn_nonprefix_items = [["assistant","Hello world","y1"], ["assistant","Different","y2"]]  ← 非前缀时正常
```

**精确修复**（`appendEventToMessages`）：
1. 仅当 `event.item_id`/`itemId` **缺席**时才查 `nativeTurnItemAliases` 兜底；显式带 item id 的事件不得用别名把目标指向另一 item。
2. `aliasOnlyMatch === true`（显式新 item id ≠ 命中行 id）时，**禁止**走 `startsWith` 替换分支，直接新增行。
3. 若产品语义确实要“一个 turn 一条 assistant 消息”，必须在 adapter/worker 层先把同 turn 多个 agentMessage 归并成一条后再下发，而不是在前端用前缀猜测。

**验收断言**：`[x1 "Hello", x2 "Hello world"]` → 两行，`nativeItemId` 分别为 `x1`/`x2`，正文不被改写；`[y1 "Hello world", y2 "Different"]` → 两行。

---

### F7 · P2【确定复现】Codex exec 兜底路径的 raw MCP/Dynamic 工具 live 投影 ≠ canonical

**代码**：`adapter.py:482-537` 的 itype 分支未包含 `mcptoolcall`/`dynamictoolcall`，落入 `else` 兜底 → `mcpToolCall({...})`；而 `sessions._item_to_block:263` 明确处理这两类 → `pan_probe({...})`。
app-server 路径不会触发（`_item_event:158` 已把 MCP 工具转成 canonical `tool_use`），但 `wrapper.py`（文档化的 exec 兜底入口）会把 raw `item.completed` 直接交给 adapter。

**证据**：`evidence/probe_protocol_provider.out.json` → `P1_codex_projection.exec_mcptoolcall_raw` / `exec_dynamictoolcall_raw`

```
live      = mcpToolCall({"tool":"pan_probe","arguments":{"x":1},"result":"ok"})
canonical = pan_probe({"x":1,"result":"ok"})
live      = dynamicToolCall({"name":"pan_probe","input":{"y":2}})
canonical = pan_probe({"y":2})
```

**精确修复**：把 `("functioncall","mcptoolcall","dynamictoolcall")` 的命名/参数/结果合并逻辑抽成一个共享 helper（放 `codex/tool_projection.py`），adapter live 分支与 `_item_to_block` 同时调用。

**验收断言**：对 raw `item.completed` 的 MCP/Dynamic 工具，`extract_assistant_blocks(event) == [_item_to_block(item)]`。

---

### F8 · P2【确定复现】前端 `pyJsonDumps` 与后端 `canonical_tool_json` 对浮点/数字的序列化不同 → 工具行文本不一致

**代码**：`useWebSocket.ts:668 pyJsonDumps` = `JSON.stringify` + `\uXXXX` 转义；后端 = Python `json.dumps(..., ensure_ascii=True, separators=(",",":"))`。
JS `JSON.stringify(30.0)` → `30`；Python `json.dumps(30.0)` → `30.0`。

**证据**：`evidence/probe_frontend_ws.out.json` → `projection`

```
frontend_tool_block = {role:"tool", content:'Sleep({"seconds":30})'}
backend_canonical   = 'Sleep({"seconds":30.0})'
mismatch            = true
```

影响面：任何没有 `nativeItemId` 的工具行（**CBC 全部工具行**）只能靠正文相等归并；浮点/科学计数法差异会让 live 行与 canonical 行在 reload/refresh 后同时存在。

**精确修复**：让前端序列化与 Python 对齐（整数型 float 输出 `N.0`，科学计数法与 Python `repr` 一致），或更彻底——**取消以序列化文本作身份**，统一用 `nativeItemId`/`messageId`/结构化 payload 归并（也需要 CBC 侧补 ID，见 F5/F10）。

**验收断言**：同一 fixture（含 `30.0`、`1e21`、非 ASCII、嵌套 dict）两侧工具 content **逐字节相同**。

---

### F9 · P2【确定复现】`codex.item.completed`（未知原生项）只活在前端，永不持久化 → 刷新后消失

**代码**：`app_server_wrapper.py:203` unknown item → `{"type":"codex.item.completed", ...}`；`adapter.py:414 is_assistant_event` 只认 `item.completed/assistant/thinking` → 不落 history；`sessions._item_to_block` 对未知类型返回 `None`；前端 `useWebSocket.ts:709 extractBlocks` 却把它渲染成 tool 行。

**证据**：`evidence/probe_protocol_provider.out.json` → `P1_codex_projection.appserver_unknown_item`

```
live_event_type = "codex.item.completed"
persisted_live  = false          # adapter.is_assistant_event
canonical       = null           # sessions._item_to_block
```

前端渲染证据：`evidence/probe_frontend_ws.out.json` → `reducer.b4_unknown_native_item` = `[{role:"tool", content:'futureNativeItem({"summary":"kept"})'}]`。

**精确修复**：明确二选一并写进契约——
(a) 持久化：adapter 增加 `codex.item.completed` 的兜底 `extract_assistant_blocks`（与 `item.completed` 的 else 分支同形），并在 `sessions._item_to_block` 补同形兜底；
(b) 瞬态：前端不把它作为普通 history 行渲染（或显示“仅本次会话可见”），并在文档中声明 reload 会消失。
当前状态（显示但不持久化）属于两者之间的**非预期默认**，必须收敛。

**验收断言**：对 `futureNativeItem`，`is_assistant_event`、`_item_to_block`、前端 `extractBlocks` 三处对“是否持久化”的结论一致（同真或同假）。

---

### F10 · P2【确定复现】CBC live 事件既无 `nativeItemId` 也无 `final`

**证据**：`evidence/probe_protocol_provider.out.json`

- `P3_cbc_shapes.final_attr_present_in_adapter_source = false`
- `P5_cbc_worker.*.stream_final_flags = [None]`、`stream_item_ids = [None]`

回答任务问题“final 属性是否真实发出”：

- **Codex：是。** `app_server_wrapper.py:691-692` 对 `item/completed` 的 assistant/thinking 事件显式 `event["final"] = True`；探针 `P2_worker_terminal.*.stream_events` 中可见 `"final": true`。
- **CBC：否。** CBC 的 `assistant` 事件是完整消息（非 delta），前端永远走 `useWebSocket.ts:938` 的“新增行”分支；`final` 的替身语义（权威完成块）完全缺失。当前没有造成重复（`b3_cbc_identical_non_final` 正确地保留两行），但**任何依赖 `final` 的收敛/去重逻辑对 CBC 都不生效**。

**修复方向**：CBC adapter 在 `extract_assistant_blocks`/worker 广播侧补 `final: true`（CBC 的 `assistant` 即完成块），并补 `nativeItemId`（JSONL/流事件里可用的 `id`）。同时前端对“无 final 的完整消息”保留现有追加语义，不要改成 delta 累计。

---

### F11 · P2【确定复现】CBC thinking 块用 `text` 键时 live 提取抛 `KeyError`

**代码**：`adapters/cbc/adapter.py:381` `content = b["thinking"]`（直接下标）。

**证据**：`evidence/probe_protocol_provider.out.json` → `P3_cbc_shapes.thinking_text_error = "KeyError: 'thinking'"`。
（CodeBuddy 是 Claude 形状，正常拼写是 `thinking`；此条按“防御性”要求 pin，不是已观测到的真实线上崩溃。）

**修复**：改为 `b.get("thinking") or b.get("think") or b.get("text")`；`text` 分支同样用 `.get`。

**验收断言**：`{"type":"thinking","thinking":"hmm"}`、`{"type":"thinking","text":"hmm"}`、`{"type":"thinking","think":"hmm"}` 三种都返回 `[{"role":"thinking","content":"hmm"}]`。

---

### F12 · P2【确定复现】WS 降载时 `worker.result` 会被静默丢弃；控制事件刷屏时连 `resync_required` 标记也不下发

**代码**：`web/server.py:854-926 enqueue` / `:937-986 _require_resync`。

**证据**：`evidence/probe_server_ws.out.json`（`evidence/probe_server_ws.log.txt` 含服务端日志）

| 场景 | 结果 |
|---|---|
| 迟滞 socket + `_WS_OUTBOUND_QUEUE_MAX+5`=69 条 **delta** + 1 条 `worker.result` | 2 帧送达：`worker.stream`(首) + `resync_required`；**`worker.result` 未送达**（`has_worker_result=false`）；日志 `slow dashboard client evicted: droppedDeltas=65 reason=outbound queue full` |
| 迟滞 socket + 69 条 **控制**事件（`queue.snapshot`）+ `worker.result` | 65 帧送达，**既无 `resync_required` 也无 `worker.result`**（`resync_required=false, has_worker_result=false`），尾部全是 `queue.snapshot`；日志 `droppedControlEvents=1` |

原因：`enqueue` 在 `not self._accepting` 时直接 `_record_drop` 返回 False；`_require_resync` 只在 `len(queue) < _WS_OUTBOUND_QUEUE_MAX` 时才追加标记（控制事件刷屏时队列已满 → 标记也不下发）。恢复依赖 `_finish_close()` 关闭 socket → 浏览器重连 → `onopen` 的权威 HTTP 刷新（`useWebSocket.ts:170-185`），因此**不是数据丢失，但实时完成事件确实丢失，且“必须 resync”的信号在控制刷屏场景缺失**。

**精确修复**：为 `resync_required` 标记与 terminal 结果预留槽位（例如队列满时允许用标记替换 1 条已排队的控制帧，或把队列上限拆成“控制帧上限 + 标记预留”），保证“要么送达 `worker.result`，要么送达 `resync_required`，不允许两者都不送”。

**验收断言**：上述两个场景都满足 `has_worker_result or has_resync_required == True`，且排队顺序中标记位于被丢弃区间之后。

---

### F13 · P2【静态推断】history wire 身份是索引派生，聚合/重复行会改变尾部的 `historyTotal` 与内容

**代码**：`web/server.py:3551 _api_history`——无 `messageId` 时 `messageId = legacy:{sid}:{epoch}:{absoluteIndex}`；worker 写入的 history 行只有 `{role, content}`（工具行多一个 `nativeItemId`），没有 `messageId`。
索引为绝对位置且 `start` 参与计算，因此在尾部 append（F1/F2/F3）时既有行的身份稳定；但**每一条新增/重复行都会让 `historyTotal` 与“最后 N 条”的构成变化**，前端 `history` 缓存的尾窗与 `lastMessage` 随之漂移。

**修复方向**：与 F1–F3 同批——为 worker 写入的每一行分配**持久** `messageId`（例如 `{history_epoch}:{seq}`，append 时单调分配并随 JSONL 落盘），旧数据在读取边界用 `legacy:{sid}:{epoch}:{index}` 兼容。禁止用 content hash（同文本多行合法）。

**验收断言**：同一 session 连续两轮完成后，前一轮每行的 `messageId` 不变；`historyTotal` 等于 canonical 行数（不含 live-only 行）。

---

### F14 · P2【未验证】Codex app-server：`turn/completed` 之后遗留的 native 通知不会被本轮消费，可能记到下一轮

**代码**：`app_server_wrapper.py:1042-1058 run_turn` 的 `while not state["done"]` 在 `turn/completed` 置 done 后立即退出；`main():1147-1153` 随即阻塞在 `pan_queue.get()`，**不再读取 `app.incoming`**。若 provider 在 `turn/completed` 之后还排入 `item/completed`，该事件会被**下一轮** `run_turn` 读到，并按下一轮的 `turn_id`/`taskSeq` 广播。

**为什么未验证**：需要真实 codex app-server 证明其存在“完成后仍发送 item 通知”的行为；本任务不调用真实模型。

**修复方向**：`run_turn` 在 done 后非阻塞 drain `self.incoming`，把属于当前 turn 的通知补发；对无法归属的通知按 `turnId` 丢弃。给 `run_turn` 增加 `turn_id` 过滤，任何 `params.turnId != state["turn_id"]` 的通知不得进入本轮 history/广播。

**验收断言**：注入 `item/completed(turn 1)` 于 `turn/completed(turn 1)` 之后、并在 turn 2 开始时执行 → turn 2 的 history/广播中不得出现 turn 1 的文本。

---

### F15 · P2【确定复现】`_hist_persisted` / generation / task / messageId 的作用域实测

用**真实持久化写入器**（把 `session.SESSION_DIR` 指向临时目录）验证 `evidence/probe_hist_persisted.py` → `probe_hist_persisted.out.json`：

| 场景 | in-memory history | `<sid>.history.jsonl` | `_hist_persisted` | `history_revision` | terminal `historyRevision` | reload 后 |
|---|---|---|---|---|---|---|
| 正常（assistant A + result A） | `[A]` | `[A]` | 1 | 1 | **1** | `[A]`, cursor 1 |
| 工具收尾（F1） | `[A, Command, A]` | `[A, Command, A]` | 3 | 3 | **3** | `[A, Command, A]` |
| result 后晚到块（F4） | `[partial, final answer, final answer]` | 同左 | 3 | 3 | **2** | 同左（3 行） |

结论：
1. **`_hist_persisted` 本身是正确的**：它精确等于已镜像行数（`len(history)`），reload 后由 JSONL 长度重新推导（`session.py:1346`、`:1521`、`:1601`）。它**不是**重复行的成因。
2. 成因是“不该 append 的行被 append 了”（F1/F2/F4），而 `_hist_persisted` 忠实把重复行**落盘**——因此 F1 的重复最终答案**能跨进程重启存活**，不是内存态假象。
3. **进程内覆盖元数据会过期**：`_hist_persisted` 与 `history_revision` 都到 3，但 `worker.result` 里声明的 `terminalCoverage.historyRevision` 仍是 2（F4）。终态声明与持久化事实不一致。
4. **作用域缺口**：`_hist_persisted` 是**每 Session、进程内**的计数，不带 `history_epoch`/task/worker 作用域；`replace_history`（import/fork 换 epoch）走 `force_full` 整写并重设游标，因此 append-only 场景安全。但 `terminal_results` 里的 `historyRevision` 是**一次性快照值**，没有与 `history_epoch` 联合校验，客户端无法仅凭它判断自己的 canonical 副本是否已覆盖终态。
5. **messageId 作用域**：worker 写入的 history 行只有 `{role, content}`（工具行另有 `nativeItemId`），wire 身份由 `_api_history` 以 `legacy:{sid}:{epoch}:{absoluteIndex}` 派生（F13）。`nativeItemId` 只覆盖 Codex 工具行，**assistant/thinking 行没有任何持久 ID**，因此终态重复行与原始行在 wire 上只能靠正文比对。

**精确修复**：为 worker 写入的每行分配持久 `messageId`（`{history_epoch}:{单调序号}`，随 JSONL 落盘），并让 `worker.result` 的 `terminalCoverage` 携带 `{historyEpoch, historyRevision, lastMessageId}`；前端只在 `historyEpoch` 相同且 `historyRevision >= terminalCoverage.historyRevision` 时才宣告该 task 收敛。

---

### F16 · P2【静态推断】generation 是进程内计数，跨进程重启从 0 重新开始

**代码**：`worker.py:292 _worker_generations: dict[str,int] = {}`、`:304 _next_worker_generation`（`get(session_id, -1)+1`）、`:6685 _worker_generations.clear()`。
同一进程内重启会 `_bump_worker_generation`（`:310`）递增，旧事件被拒；完整进程冷重启后 generation 从 0 重新开始，可能小于浏览器残留的旧值。

**当前缓解**：`ws.ts:107-113` 在 `serverEpoch` 变化时发 `server_epoch_changed`；`sessionStore.ts:1481 acceptServerEpoch` 清空 `terminalWatermarks` 与 `liveStreamBuffers`（V5），因此旧 generation 水位不会残留。**但该缓解依赖 epoch 变化被客户端观测到**：如果浏览器在服务重启期间保持页面打开但从未收到任何带 epoch 的帧（例如只有 `pong`），`eventEpoch` 仍为 `null`，`acceptServerEpoch` 不会被触发；此时若浏览器本地已有 `terminalWatermarks`（来自重启前），需要另行确认。本任务未做真实冷重启 E2E，故标为静态推断。

**精确修复**：`isOlderMeta`（`sessionStore.ts:370-372`）已经“epoch 不同即视为旧”，属于正确方向；建议把 `serverEpoch` 设为所有 runtime 比较的**第一作用域**，并在没有 epoch 的旧帧上**拒绝**推进任何水位（而不是回退到 generation 比较）。冷重启验收需真实 FastAPI/WS/Chromium，见上一轮报告的验收矩阵。

---

## 3. 已验证为正确（不要再去追）

| 编号 | 结论 | 证据 |
|---|---|---|
| V1 | `worker.result` 永远在最后一个 assistant `worker.stream` 之后；`eventSeq`/`deliverySeq` 单调 | `probe_server_ws.out.json` → `ordering` = stream(1) → stream(2) → stream(3, final) → result(4) |
| V2 | 相邻同 key delta 合法合并为一帧，携带 `sourceCursorStart..End` 范围与累计文本；**非前缀累计拒绝合并** | `probe_server_ws.out.json` → `coalesce`（3 delta → 2 帧，`sourceCursorStart=6,End=7,stream_text="ABC"`）、`bad_coalesce_boundary`（`AB`/`XY` 两帧不合并） |
| V3 | 客户端不会因合法合并误判 gap；重复 deliverySeq 被丢弃；真 gap 触发一次 resync；快照清除 `resyncPending` | `probe_frontend_ws.out.json` → `contiguous_then_coalesced_resync=0`、`duplicate_delivery_dispatched=0`、`after_gap_resync=[delivery_cursor_gap]`、`resyncPendingAfterSnapshot=false` |
| V4 | **“快照 eventSeq 过期导致倒挂”不可复现**：`_stamp_live_event` 的 `(自增, enqueue)` 与 `_resync_snapshot` 的 `(读取, enqueue)` 都在同一次事件循环内同步完成，单事件循环下线上顺序 == seq 顺序 | `probe_server_ws.out.json` → `snapshot_order` = 11 → snapshot(11) → 12（单调）；client probe `epoch_changes` 正常 |
| V5 | epoch 变化会清空 `terminalWatermarks`/`liveStreamBuffers`（上一轮 F04 的冷重启拒绝已缓解） | `ws.ts:107-113` 发 `server_epoch_changed`；`sessionStore.ts:1481 acceptServerEpoch` 清 runtime 水位；`useWebSocket.ts:204-207, 212-213` 两处都调用 |
| V6 | Codex 工具 live == canonical（command / mcp / filechange，含中文与嵌套）——**HEAD 的修复生效** | `probe_protocol_provider.out.json` → `P1_codex_projection.{command,mcp,filechange}.identical = true` |
| V7 | Codex live 的 `final` 属性**真实发出**（completed assistant/thinking/tool 项） | `app_server_wrapper.py:691-692`；`P2_worker_terminal.*.stream_events` 可见 `"final": true` |
| V8 | Codex delta 累计文本是**每 item 局部**的，不会被其它 item 污染 | `tests/test_codex_adapter.py::test_app_server_cumulative_text_is_item_local`（exit 0）；`P1` 中 mcp/command 结构一致 |
| V9 | CBC 两条相同文本的**非 final** assistant 事件正确地产生两行（未被文本去重误吞） | `probe_frontend_ws.out.json` → `reducer.b3_cbc_identical_non_final` = 两行 |
| V10 | 既有相关测试在基线上全绿（说明上述缺陷没有对应覆盖） | `evidence/baseline_test_*.txt`，11 个文件全部 exit 0 |

---

## 4. 给下一位 TA 的实施方案

### 4.1 修复顺序（每步可独立验收，禁止一步重写两层）

| 步骤 | 内容 | 依赖 | 必须产出的证据 |
|---|---|---|---|
| S1 契约先定 | 明确三件事：(a) `result` 是“最后一个 assistant 块”还是“聚合文本”；(b) 同一 turn 多 assistant item 是一个消息还是多个；(c) 未知原生项是持久化还是瞬态 | 无 | 契约文档 + 本文件 F1/F2/F6/F9 的选择结论 |
| S2 worker terminal 覆盖 | F1 + F2 + F4（覆盖检查、晚到帧策略、coverage 与 revision 一致） | S1 | `--runxfail` 从 exit 1 变 exit 0；F1/F2/F4 三个 xfail 标记删除 |
| S3 前端 terminal 收敛 | F3 + F2 的前端半边（幂等键、禁止整段替换、去重） | S2 | reconcile 探针的三场景全部无重复；`historyTotal == len(history)` |
| S4 provider 投影统一 | F5（CBC 共享 `tool_projection`）+ F7（Codex exec 兜底）+ F11（thinking 容错） | S1 | `live == canonical` 的联合 fixture（ASCII/嵌套/非 ASCII/长参数/多 text 块/三种 thinking 键） |
| S5 身份与显示 | F8（数字序列化对齐或改结构化身份）+ F9（未知项生命周期）+ F10（CBC 补 `nativeItemId`/`final`）+ F6（前端别名不劫持） | S4 | 前端探针 b6/b7 复用；浮点 fixture 两侧逐字节相同 |
| S6 传输降载 | F12（结果/标记预留槽位） | 无（可并行） | 两个降载场景都满足 `result or resync_required` |
| S7 持久身份 | F13（worker 写行分配持久 `messageId`） | S2/S3 | 两轮完成后前一轮 `messageId` 稳定 |
| S8 Codex wrapper drain | F14（先取得真实 app-server 证据，再改） | 真实 provider | 若无法取得证据，保持现状并在文档标注为已知窗口 |

### 4.2 验收矩阵（每项同时断言 **身份序列 / role / 精确正文 / historyTotal / coverage**，禁止只比总条数或最后字符串）

| 场景 | 注入 | 必须断言 |
|---|---|---|
| Codex 工具收尾 | `assistant "A"` → `tool Command` → `result "A"` | assistant 行恰 1 条；行序 `[assistant, tool]`；`historyRevision == terminal.historyRevision` |
| 同上 · 落盘复证 | 真 `SESSION_DIR` 跑同一序列 | `<sid>.history.jsonl` 恰 2 行；reload 后仍 2 行；`_hist_persisted == len(history)` |
| terminal 后晚到块 · 落盘复证 | `result` 后追加 assistant | JSONL 行数 == `history_revision`；`worker.result.historyRevision` == 落盘行数（或已按 S1 拒绝该帧） |
| 聚合 result | 两条 assistant 块 + 聚合 `result` | assistant 行 = 两条原始块；`currentMessages` 无重复；`historyTotal == len(history)` |
| 无 live assistant | 仅 `result` | 可见 result 恰 1 次 |
| terminal 后晚到块 | `result` 后追加 assistant | 依 S1 选择：要么被拒且无副作用，要么发出新 coverage 且 `taskId` 非空；**不允许** coverage 与 `history_revision` 不一致 |
| CBC 工具 | `function_call` ASCII/嵌套/中文/600 字符 | `live == canonical`，无 500 字符单侧截断 |
| CBC 多 text 块 | 两个 text 块含首尾空白 | 两侧块数/正文一致 |
| CBC thinking | `thinking`/`think`/`text` 三种键 | 均得 `[thinking "hmm"]` |
| Codex 同 turn 双 item | `x1 "Hello"`, `x2 "Hello world"` | 两行，nativeItemId 分别 x1/x2 |
| Codex exec 兜底 | raw `item.completed` mcp/dynamic | `live == canonical` |
| 未知原生项 | `futureNativeItem` | adapter / sessions / 前端对“是否持久化”结论一致 |
| 浮点工具参数 | `{"seconds":30.0}` | 前后端工具 content 逐字节相同 |
| delta 洪泛 + result | 69 delta（`MAX+5`）+ `worker.result` | `worker.result` 或 `resync_required` 至少送达一个 |
| 控制洪泛 + result | 69 控制帧（`MAX+5`）+ `worker.result` | 同上 |
| 快照/事件交错 | broadcast → resync → broadcast | 线上 `eventSeq` 单调（回归 V4） |
| epoch 变化 | 换 `serverEpoch` 发新 worker 事件 | `terminalWatermarks`/`liveStreamBuffers` 被清空，新帧被接受 |

### 4.3 必须保留、不得回退的现有行为

- **terminal 持久化屏障**：`_persist_terminal_state` 的 base commit 成功后才广播 `worker.result`→`idle`；基础持久化失败不得发完成广播。
- **`worker.result` 先于 `worker.status(idle)`**，且 usage enrichment 不阻塞二者（V1）。
- **合法合并不触发恢复**：不得为了消除“合并”而删除 source cursor 的范围语义（V2/V3）。
- **epoch 变化清 runtime 水位**（V5）；不得改成“generation 小就接受”。
- **前端 terminal guard**（`canApplyLiveStream` / `isBlockedByTerminal`）保留，只允许按 task+item 细化作用域。
- **Codex 工具 canonical 化**（head 修复，V6）不得回退成两套序列化。

---

## 5. 命令与退出码（原样记录）

```
# Git 基线
git rev-parse --show-toplevel      -> D:/project/pan-worktrees/frontend-reaudit-protocol-ds-20260921
git rev-parse HEAD                 -> 591367a65f88e9d5270e8e99920ef5448d1d68c9
git rev-parse --git-common-dir     -> D:/project/Pan/.git
git status --porcelain             -> 空（审查开始时干净）

# 探针
E:/software/miniforge/python.exe evidence/probe_protocol_provider.py > evidence/probe_protocol_provider.out.json   exit 0
E:/software/miniforge/python.exe evidence/probe_server_ws.py evidence/probe_server_ws.out.json                   exit 0
E:/software/miniforge/python.exe evidence/probe_hist_persisted.py evidence/probe_hist_persisted.out.json          exit 0
node evidence/probe_frontend_ws.cjs > evidence/probe_frontend_ws.out.json                                        exit 0
node evidence/probe_frontend_reconcile.cjs > evidence/probe_frontend_reconcile.out.json                          exit 0

# 新增回归测试
E:/software/miniforge/python.exe -m pytest tests/test_reaudit_protocol_provider.py -q                     exit 0  (9 passed, 10 xfailed)
E:/software/miniforge/python.exe -m pytest tests/test_reaudit_protocol_provider.py --runxfail --tb=line   exit 1  (10 failed = 复现证据)
E:/software/miniforge/python.exe -m pytest tests/test_reaudit_frontend_reconcile_order.py -q      exit 0  (1 passed, 2 xfailed)
E:/software/miniforge/python.exe -m pytest tests/test_reaudit_frontend_reconcile_order.py --runxfail -q  exit 1 (2 failed)
E:/software/miniforge/python.exe -m pytest tests/test_reaudit_protocol_provider.py -v -rxX -p no:randomly exit 0

# 既有相关测试基线（全部 exit 0，见 evidence/baseline_test_*.txt）
test_codex_adapter / test_codex_worker_integration / test_cbc_models / test_cbc_oneshot_args /
test_cbc_import_guard / test_delivery_semantics / test_terminal_broadcast / test_ws_backpressure /
test_replay_resync / test_worker_history / test_steer_history_consistency
```

`--runxfail` 的原始失败逐字摘要（`evidence/pytest_reaudit_runxfail.txt`，pytest 用 `...` 截断长 repr）：

```
E   AssertionError: assert ['A', 'A'] == ['A']
      Left contains one more item: 'A'                                     # F1
E   AssertionError: assert ['first half ... second half'] == ['first half ', 'second half']
      Left contains one more item: 'first half second half'                # F2
E   AssertionError: assert 2 == 3     (session.last_result['historyRevision'] vs history_revision / jsonl rows)   # F4
E   assert [{'role': 'to...limit":10})'}] == [{'role': 'to...imit": 10})'}]
      At index 0 diff: {'role': 'tool', 'content': 'Read({"path":"a.txt","limit":10})'}
                    != {'role': 'tool', 'content': 'Read({"path": "a.txt", "limit": 10})'}   # F5 tool
E   assert [{'role': 'as...t': 'line2 '}] == [{'role': 'as...ine1\nline2'}]
      At index 0 diff: {'role': 'assistant', 'content': ' line1\n'}
                    != {'role': 'assistant', 'content': 'line1\nline2'}
      Left contains one more item: {'role': 'assistant', 'content': 'line2 '}                  # F5 multi-text
```

---

## 6. 边界与未验证项

- 未调用任何真实模型/真实 CLI；Codex app-server 与 Codex exec 的真实事件序列来自代码与现有 fixture，未抓取真实 rollout。
- 未启动服务、未访问 8768、未使用真实浏览器；前端结论来自真实模块在 Node 中的执行（esbuild 内存打包 + React/zustand 依赖桩），**不等同于完整 React 渲染 / 真实 WebSocket / 真实 provider E2E**。
- F14（wrapper 遗留通知）为**未验证**：需要真实 app-server 证据。
- 未在各 provider 上抽查 `result` 的真实聚合/末块语义（Codex 两条路径可从代码判定；CBC/其他 CLI 需真实 fixture）。F1/F2 的修复必须先敲定 S1 契约，否则可能把错误语义固化。
- 未运行完整 `pytest tests/` 与前端 `vitest` 全量；只运行了与本次改动相关的子集，均 exit 0。
- 本报告不构成“已修复/可合并/可部署”的证明；所有修复仍需在指定隔离 worktree 上实现并重新验收。

---

# 7. 补充审查（主审复核项）：reconcile 重排/重复、真实 CBC 身份、result↔最后 assistant、ID 映射方案

本节新增证据：`evidence/probe_real_cbc_schema.*`、扩展后的 `evidence/probe_frontend_reconcile.out.json`、
`evidence/probe_protocol_provider.out.json`（新增 P7/P8）、`evidence/pytest_reaudit_frontend_order*.txt`、
`tests/test_reaudit_frontend_reconcile_order.py`。

## 7.1 reconcileWorkerResult 会把一个 turn 重排、并在 idless 时整轮重复【确定复现】

主审的说法成立，但需要一个明确前置条件；且“idless 时 final 双份”只是最轻表现，实际会**整轮重复**。

`reconcileWorkerResult`（`sessionStore.ts:1682`）的顺序是**先写 `session.history`，再 merge live**：

1. 把 `result` 追加到**客户端缓存的** history **末尾**（`:1748-1757`），或按 `liveIds`/`legacyTailExact` 原地替换（`:1724-1747`）；
2. `canonical = mergeServerHistoryWithLive(session.history, finalLiveMessages)`（`:1773`）；
3. `mergeServerHistoryWithLive`（`:325`）对每条 live 行按**显式身份**找 canonical 行；**找不到就 append 到末尾**（`:344-351`）。

当客户端缓存的 `session.history` **落后于 live turn**（典型中间态：只加载到 user 行）时，第 1 步先把 result 插到末尾，
第 2 步再把尚未进入缓存的 analysis/tool 追加到它**后面** → result 行“跑到” analysis/tool 之前。

实测（`evidence/probe_frontend_reconcile.out.json`）：

| 场景 | currentMessages |
|---|---|
| `partial_history_with_ids`（缓存只有 user；live=`[analysis,tool,final]`；result=`final`） | `[user, final, analysis, tool]` ← **正是主审给出的重排** |
| `partial_history_idless`（同上但无任何身份，真实 CBC） | `[user, final, analysis, tool, final]` ← 重排 **+** final 重复 |
| `ordered_turn_idless_result_equals_last`（history 已完整、无身份） | `[user, analysis, tool, final, analysis, tool, final]` ← **整个 turn 被追加第二遍** |
| `ordered_turn_idless_result_differs` | 8 行：canonical 保留聚合行，且整轮重复 |
| `ordered_turn_with_ids_result_equals_last` / `..._differs` | 顺序正确、无重复（有身份时唯一被覆盖的是最后一行正文） |

原始断言失败（`evidence/pytest_reaudit_frontend_order_runxfail.txt`，exit 1）：

```
At index 1 diff: ('assistant', 'final') != ('assistant', 'analysis')              # 重排
Left contains 3 more items, first extra item: ['assistant', 'analysis', None]     # 整轮重复
```

纠正：无身份的 live 行**全部无法归并**，会把当前 turn 整轮再追加一遍（并带上被 result 改写后的正文），
不只是 final 双份。

精确修复（`reconcileWorkerResult`）：
1. 取消 `history.push(result)` 的“追加到缓存末尾”。先定位本 turn 锚点 = history 中第一个属于该 turn 的行
   （用 `taskSeq`/`turnId`/`blockId` 判定）或 history 末尾；把 result 行 **splice 到该 turn 最后一行之后**。
2. merge live 时，未命中的 live 行必须插入到**同 turn 锚点区间内的正确序号**（按 `blockIndex`/`localSeq`），
   不得一律 append；同 turn 的 canonical 行必须排在同 turn 的 live-only 行之前。
3. 身份缺失时只允许“同 turn 内、受限候选、一对一”匹配并记录不确定性；禁止全历史文本兜底，也禁止无条件 append。
4. result 与 `liveAssistant` 的对应关系不得再靠“最后一个 assistant 行”推断（见 7.3）。

## 7.2 真实 CBC 的身份在哪里丢失（真实数据，不是人工构造字段）【确定复现 + 真实数据扫描】

数据来源：本机 `~/.codebuddy/projects/**/*.jsonl`，**103 个真实会话 / 8305 行**，仅统计键名与 id 存在性，
不含正文（`evidence/probe_real_cbc_schema.out.json`）。

真实 CBC 转录 schema：

```
type='message'               keys: id, parentId, sessionId, role, content, message, providerData, status, cwd, timestamp, _meta
type='function_call'         keys: id, parentId, callId, sessionId, name, arguments, message, providerData, ...
type='function_call_result'  keys: id, parentId, callId, sessionId, name, output, status, ...
type='reasoning'             keys: id, parentId, sessionId, content, rawContent, providerData, ...
block 'input_text'           keys: type, text
block 'output_text'          keys: type, text, providerData
block 'image_blob_ref'       keys: type, blob_id, blob_path, mime, size
```

身份字段实测出现次数：`function_call.id`=2445、`function_call_result.id`=2414、`message.id`=1142、`reasoning.id`=1385，
另有 `callId`（工具调用）与 `parentId`（父子链）。
**关键**：身份只在**信封（top-level）**上；嵌套 `message` 对象**只有 `usage`**（无 `id`、无 `content`）。

逐层丢失点：

| 层 | 位置 | 丢失内容 |
|---|---|---|
| live 提取 | `adapters/cbc/adapter.py:375 extract_assistant_blocks` 只读 `event["message"]["content"]` 的 text/thinking/tool_use，输出 `{role, content}` | 信封 `id`、`parentId`、`message.id`、`tool_use.id` |
| canonical 解析 | `adapters/cbc/sessions.py:685 _event_to_block` 只读信封 `content` / `message.content`，输出 `{role, content}` | 信封 `id`、`parentId`、`callId` |
| worker 落盘 | `worker.py:1527-1531` `append_history(s, b)` 原样写入 adapter 给的块 | 无 id 可写（上游已丢） |
| WS 广播 | `worker.py:1580` 广播原始 event，信封 `id` 仅作为不透明 payload 到达浏览器 | 前端不消费 |
| 前端块提取 | `useWebSocket.ts:722` 只读 `message.content`/`content` | 信封 `id`、`message.id`、`tool_use.id` |
| 前端身份 | `useWebSocket.ts:830,841` `nativeItemId` 仅取自 `event.item_id`/`turn_id`（Codex 专用） | CBC 行全部无身份 |
| wire | `server.py:3551 _api_history` 无 `messageId` → `legacy:{sid}:{epoch}:{absoluteIndex}` | 位置派生身份 |

代码级证明（`evidence/probe_protocol_provider.out.json` → `P8_identity_trace`）：`cbc/{__init__,adapter,sessions}.py`
中 `.get("id")` / `callId` / `parentId` / `nativeItemId` 出现次数**全部为 0**；喂入带身份证的真实形状输入后：

```
canonical message block : {"role": "assistant", "content": "hello"}
canonical function_call : {"role": "tool", "content": "Read({\"file_path\": \"a.txt\"})"}
canonical reasoning     : {"role": "thinking", "content": "because"}
canonical kept any id   : False
live blocks             : [{"role":"assistant","content":"hello"},{"role":"tool","content":"Read({\"file_path\":\"a.txt\"})"}]
live kept any id        : False
wire messageIds         : legacy:cbc-session:epoch-1:0 / :1 / :2 / :3   (index-derived = True)
```

未验证：真实 CLI 的 live stream-json 帧是否真的携带 `message.id`/`tool_use.id`。归档的真实探测脚本
`docs/archive/cbc-mcp-experiments/multiround_probe.py` 证实 live 形状是 Anthropic Messages 形状，但未记录 id 字段。
JSONL 侧身份**已确证存在**，故 canonical 解析器丢 id 是确证结论；live 侧“即使有 id 也会丢”是代码确证，是否真有 id 待真实抓帧。

## 7.3 result 是否可靠对应“最后 assistant”：不可靠（3/5 真实形状失败）【确定复现】

CBC live 侧把**一条 assistant 消息拆成多个块**（每个 text 块一行），而 CLI 的 `result` 是**整条消息的文本**，二者粒度不同。

`evidence/probe_protocol_provider.out.json` → `P7_cbc_result_alignment`：

| 真实 CBC 形状 | 落盘 assistant 行 | result 对应 | 结论 |
|---|---|---|---|
| 一条消息含 2 个 text 块，result=拼接 | `["part1 ", "part2", "part1 part2"]` | 新增聚合行 | 不对应（多出第 3 行）；result 也不等于最后 live 块 `part2` |
| 两条独立 assistant 消息，result=最后一条 | `["first", "second"]` | 最后一行 | 可靠 |
| 最后一条消息只有工具，result 复用早前文本 | `["let me look", "let me look"]` | 早前那一行 | 不对应且重复 |
| result 复用更早的文本（不是最后一条） | `["A", "B", "A"]` | 第一行 | 不对应 |
| 一条消息 thinking+text，result=text | `["answer"]` | 最后一行 | 可靠 |

根因：`worker.py:972-976` 只用“最后一行是否同文本”这一条判据；且 CBC 的 result 是**消息粒度**，行是**块粒度**。

结论：`result` 目前只在“最后一个 assistant 消息恰有 1 个 text 块且 result 等于该块”时可可靠落到最后一行；
其余真实形状会产生聚合重复行，或把 result 记到**非最后**的行上。因此不能用 `result` 推断“最后一条 assistant 消息”。

修复方向：
- adapter 新增 `extract_result_target(event) -> {nativeItemId|blockId, text}`，让 result 显式携带其对应的**消息身份**；
- worker 用该身份定位/升级对应行；无身份时按“本 turn 最后一个 assistant **消息**（可能多块）”整组比对，而非单行比对；
- 若 provider 的 result 语义确实是“整条消息文本”，worker 应把该消息各块按 provider 原文合成后再比对，而不是新增第 3 行。

## 7.4 精确映射方案：native item / local sequence / history API ID

目标：每一行都有唯一持久身份，跨 live→落盘→reload→wire→merge 不变；禁止以正文或索引作为主身份。

| 概念 | 字段 | 生成者 | 规则 | 用途 |
|---|---|---|---|---|
| provider 原生项 | `nativeItemId` | adapter（live+canonical 同一 helper） | Codex `item.id`；CBC **信封 `id`**，工具项用 `callId`（回退信封 `id`）；Claude/Kimi `message.id`/`tool_use.id`；缺失时合成 `provider:{cli_session_id}:{turnIndex}:{ordinal}` | 归并同一 provider 项的多次通知 |
| 显示块 | `blockId` | adapter（或 server 首次读取时补齐） | `${sid}:${turnKey}:${nativeItemId}#${blockIndex}`；`turnKey` 优先 `taskSeq`，其次 `taskId`，再次 `turnId`；无 `nativeItemId` 时用合成 `provider:...` 值，**绝不用正文 hash** | React key / 归并主键（`messageIdentity.ts` 已优先 `blockId`） |
| 本地临时行 | `nativeItemId` = `local:user:${sid}:${n}`（已有）/ `live:${sid}:${n}`（新增） | 前端 | 仅用于乐观 user 与服务端尚未落盘的 live 行；单调 `localSeq` | 排序与一对一匹配候选，**不是**持久身份 |
| 持久行身份 | `messageId` | **worker append 时分配并落盘** | `${history_epoch}:${history_seq}`，`history_seq` 每 session 单调，随 JSONL 行写入 | wire 身份、分页锚点、终态覆盖判定 |
| 兼容身份 | `messageId = legacy:{sid}:{epoch}:{absIndex}` | server | 仅用于无 `messageId` 的历史行；首次读到/写回时补写为持久 `messageId`（惰性迁移），避免位置漂移 | 旧数据兼容 |
| 终态覆盖 | `terminalCoverage` | worker → `worker.result` | `{historyEpoch, historyRevision, lastMessageId, coveredBlockIds?}` | 前端宣告收敛的唯一依据 |

排序权威：canonical 顺序 = 持久化顺序（JSONL 行序）。live 行是按 `blockId` 定位到 canonical 槽位的**覆盖层**；
尚未进入 canonical 的 live 行必须插到**同 turn 锚点区间内**（按 `blockIndex`），而不是一律 append 到 history 末尾。

各层改动点：

1. 新增 `adapters/cbc/tool_projection.py`：承接 F5 的统一工具序列化。
2. `adapters/cbc/adapter.py::extract_assistant_blocks` 与 `adapters/cbc/sessions.py::_event_to_block`：读取信封 `id`/`callId`/`parentId`，
   统一输出 `{role, content, nativeItemId, blockId, blockIndex}`；两侧共用同一 helper（否则 F5 的序列化分裂会再犯）。
3. `packages/core/session.py::append_history`：分配 `messageId = ${history_epoch}:${history_seq}` 并写入行。
4. `worker.py::_read_stdout`：从 adapter 取 `blockId`；`_persist_terminal_state` 的 result 处理改用
   `terminalCoverage.lastMessageId` 与消息粒度比对（7.3）。
5. `server.py::_api_history`：优先返回持久 `messageId`；对 legacy 行回写（惰性迁移）。
6. `useWebSocket.ts::appendEventToMessages`：`nativeItemId` 取值改为通用 `item_id|itemId|blockId|message.id`，
   CBC 不再无身份；把 `blockId` 传入 `Message`。
7. `sessionStore.ts::reconcileWorkerResult`：按 7.1 的锚点/splice 规则重写；`mergeServerHistoryWithLive` 未命中时按 turn 锚点插入。

验收断言：

```python
# A. 无身份 provider 不得整轮重复
assert currentMessages == [user, analysis, tool, final]
# B. 缓存落后不得重排
assert [r.content for r in currentMessages] == ["q", "analysis", "Read(...)", "final"]
# C. CBC 身份贯通
assert row.get("nativeItemId")                       # canonical 行
assert block["blockId"].startswith(f"{sid}:")        # live 块
# D. wire 身份持久且稳定
assert row["messageId"] == "epoch-1:17"              # reload 后同一行 id 不变
# E. result 对齐
assert result_target["nativeItemId"] == last_assistant_message_id
assert len([r for r in assistant_rows if r.content == result]) == 1
```

## 7.5 本节命令与退出码

```
E:/software/miniforge/python.exe evidence/probe_real_cbc_schema.py                                            exit 0  (103 files / 8305 lines)
E:/software/miniforge/python.exe evidence/probe_protocol_provider.py > evidence/probe_protocol_provider.out.json  exit 0
node evidence/probe_frontend_reconcile.cjs > evidence/probe_frontend_reconcile.out.json                        exit 0
python -m pytest tests/test_reaudit_protocol_provider.py -q                     exit 0  (9 passed, 10 xfailed)
python -m pytest tests/test_reaudit_protocol_provider.py --runxfail --tb=line    exit 1  (10 failed = 复现证据)
python -m pytest tests/test_reaudit_frontend_reconcile_order.py -q              exit 0  (1 passed, 2 xfailed)
python -m pytest tests/test_reaudit_frontend_reconcile_order.py --runxfail -q    exit 1  (2 failed = 复现证据)
```

边界：7.1 的重排是**组件级**复现（真实 sessionStore reducer + 受控 state），未在真实浏览器/真实 provider 流中复现；
7.2 的 JSONL 身份是真实数据确证，live 帧是否携带 id 未验证；
7.3 的五个形状来自真实 CLI 的 live 形状（`multiround_probe.py` 归档证据）+ adapter 代码，未调用真实模型。
