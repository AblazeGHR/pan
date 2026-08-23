# 设计：消息发送队列（vanilla 前端优先）

> 状态：**已实现（2026-08-21 ~ 08-22）**——vanilla（commit `68859d1`、`4d99dc6`）与 React（commit `236d672`）两侧均已落地。
> 适用路由：`/`（vanilla，`packages/web/ts/app.ts`）与 `/react/*`（React `src/components/chat/SendQueuePanel.tsx`）。
> 目标：聊天输入框加 `^` 按钮 → 展开"待发送消息队列"面板；每条支持删除 / 编辑 / 向上 / 向下。
> 实现形态：客户端队列（localStorage `pan.sendQueue.<sessionId>` 持久化），服务端零改动；服务端队列为远期混合方向，尚未实现。

---

## 1. 现状调研

### 1.1 前端发送链路（`packages/web/ts/app.ts`）

`send()`（app.ts:1511-1592）流程：

1. 校验 `currentSessionId`、`text` 非空。
2. **busy 检查**（app.ts:1520）：`s.workerStatus === 'running' || 'held'` → `toast('Worker is busy')` 直接拒绝。**这是当前"worker 忙时不能发"的唯一人为闸门。**
3. 清空输入框 + 删除该 session 的草稿 `_inputDrafts`。
4. 乐观上屏：`addMessage('user', text)`（app.ts:1144，push 进 `currentHistory` + 渲染）。
5. `doSend()` 闭包：构造 `{type:'user_inject', sessionId, text}` 经 WS 发送；WS CONNECTING 则等 `open`；CLOSED 则放弃并 toast。
6. 无 worker → `POST /api/spawn` 成功后 `doSend()`；有 worker 但面板有未应用设置 → 先 `POST /api/worker/{id}/settings` 再 `doSend()`。

### 1.2 后端 `user_inject` 与 `send_task`（关键发现）

- WS 入口（`packages/web/server.py:645`）：`user_inject` → `worker.send_task(wid, text, source="user")`；无 worker 则自动 `create_worker` 再 send_task。
- **`send_task`（`packages/core/worker.py:1659`）在 worker `running` 时并不拒绝**：仅拒绝「worker 不存在 / `held`（takeover）/ 进程死 / 信号队列未就绪」。`running` 状态下会直接把任务塞进 `w.pending_signal`（内存 `asyncio.Queue`），并仅在 `idle`/`queued` 时置 `queued` 并广播 `worker.status`。
- consumer 循环（worker.py:524-565）**串行消费**：`pending_signal.get()` 取一个处理一个，`_consumer_mcp` / `_consumer_stream` 阻塞到任务完成（result 事件 → 置 `idle`，worker.py:452）才取下一个。

> **结论**：服务端**已经具备"一个 worker 同一时刻只跑一个任务，后续消息自动排队"的机制**。用户想要的"先排队、空闲后自动发送"在服务端是现成的——真正需要补的是：前端可见、可编辑、可重排、可靠（持久化）的队列层。

### 1.3 服务端两个"队列"概念辨析

| 概念 | 位置 | 内容 | 用途 | 是否同概念 |
|---|---|---|---|---|
| `Worker.pending_signal` | worker.py:134 | `asyncio.Queue`，项含 `{text, source, seq, taskId}` | 任务派发信号队列，串行消费 | **接近**：用户消息也走这里排队，但内存态、不可见、不可编辑重排 |
| `Session.queue_pending` | session.py:54 | 落盘 `list`，报告项 `{status, result, sessionId, ...}` | **订阅制报告**（被管 agent 完成 → 推送给 manager 消费），立项 4.3/4.7 | **不同**：是 agent→manager 的报告投递队列，与"用户要发送的消息"无关 |

`pending_signal` 是内存队列：worker 死亡 / 空闲回收（`_WORKER_IDLE_SEC=300s`，watchdog）/ 重启都会丢。`queue_pending` 虽落盘，但语义是报告消费，不承载用户消息。

### 1.4 worker 状态机与前端同步

- 状态：`idle`（等待输入）/ `running`（处理中，worker.py:896,941）/ `queued`（已入队待消费）/ `held`（takeover，send_task 硬拒）/ `zombie` / `error` / `done`；前端无 worker 时显示 `offline`。
- 前端同步：WS `worker.result` → `_applyWorkerUpdate(sid, wid, 'idle')`；WS `worker.status` → `_applyWorkerUpdate(sid, wid, d.status)`（app.ts:345-351）。
- **`_applyWorkerUpdate`（app.ts:366）是"worker 变空闲 → 自动发送队首"的最佳 hook 点**：`status === 'idle'` 且当前 session 队列非空 → flush。

### 1.5 输入框 UI（`packages/web/index.html:76-80`）

```html
<div id="inputRow">
  <input id="chatInput" placeholder="Type a message…" onkeydown="if(event.key==='Enter')send()">
  <span id="mobileWorkerDot" class="s-dot offline"></span>
  <button type="button" onclick="send()"><span style="pointer-events:none">Send</span></button>
</div>
```

`#inputRow` 位于 `#messages` 与页面底部之间；`^` 按钮放在 `chatInput` 左侧（inputRow 首元素）。

---

## 2. 核心决策

### 2.1 队列归属：客户端队列（首版），服务端队列（远期混合）

**决策：首版用客户端队列**——前端维护"待发送列表"，localStorage 持久化；busy 时入队，worker 空闲时自动逐条 flush 到现有 `user_inject` 链路。

理由：
- 任务目标明确「vanilla 前端优先、不实现」；查看 / 截断 / 编辑 / 删除 / 重排全部是 UI 操作，客户端天然最合适，**零后端改动**即可上线。
- 服务端 `pending_signal` 已保证串行消费，客户端逐条发不会乱序，也不依赖新增后端能力。
- 相比服务端队列省去：Session 模型扩展、新 CRUD API、重排/持久化一致性、`held` 语义处理等一轮大改动。

代价与缓解：
- 队列只在当前浏览器有效 → **localStorage 按 sessionId 持久化**，页面刷新/重开恢复；换设备不同步（接受）。
- 页面关闭后队列不会自动发 → 面板与队列项明确标记「待发送」，避免误以为已发。

**远期混合方案（React 阶段路线）**：服务端加 `Session.send_queue`（落盘 `list`）+ CRUD API（`GET/PATCH/DELETE /api/sessions/{id}/queue`），客户端面板退化为服务端队列的镜像：入队/重排/编辑/删除发 API，`worker idle` 时服务端或客户端触发发送。客户端管交互、服务端管可靠投递，多端共享。实现时注意与 `pending_signal` / `queue_pending` 的边界（`send_queue` 只存"用户待发消息"，`pending_signal` 保持派发信号角色）。

### 2.2 触发时机：自动为主 + 重排/手动即时触发

- **自动（主）**：worker 状态变为 `idle` 且队列非空 → 自动发送队首 1 条。发送后 worker 变 `queued`/`running`，不再是 `idle`，天然防重复；上一条完成（`worker.result` → idle）再发下一条。**串行、不连发多条塞爆上下文。**
- **手动（辅）**：面板「↑」把某条提到队首且当前空闲 → 立即发送；面板可选「发送全部」按钮（逐条发）。
- **不发的情形**：worker `held`（takeover 模式，服务端硬拒）→ 队列保留，等用户 restart 恢复；`queued` / `running` → 不触发（由 idle 事件驱动）。

### 2.3 数据模型

```ts
interface QueuedMessage {
  id: string;        // crypto.randomUUID()，唯一标识（重排/编辑/删除的 key）
  text: string;      // 原文（渲染时截断，存全文）
  createdAt: number; // Date.now()，入队时间戳
  status: 'pending'; // 首版恒 pending；预留字段（未来服务端同步可加 'sending'/'done'）
}
```

- 存储：`localStorage`，key `pan.sendQueue.<sessionId>`，值为 JSON 数组。
- 内存镜像：`_queueCache: Map<string, QueuedMessage[]>`（按 sessionId 隔离）。
- 生命周期：发送成功的项**立即从队列删除**（队列只存"待发送"，不含历史）；切换 session 时从 localStorage 恢复当前 session 队列。
- 上限：每 session 队列上限 50 条，超出 toast 拒绝（防 localStorage 膨胀与误操作）。

### 2.4 UI / 交互

```
┌──────────────────────────────────────────────────┐
│  (messages 区)                                    │
├──────────────────────────────────────────────────┤
│  #queuePanel (默认折叠)                            │
│  ┌────────────────────────────────────────────┐  │
│  │ 待发送 (2)                        [清空]     │  │
│  ├────────────────────────────────────────────┤  │
│  │ ┌────────────────────────┬───────────────┐ │  │
│  │ │ 这是一条被截断的长消息内容…│ ✎  ↑  ↓  🗑 │ │  │  ← hover 显示按钮
│  │ ├────────────────────────┼───────────────┤ │  │
│  │ │ 第二条待发送的消息       │ ✎  ↑  ↓  🗑 │ │  │
│  │ └────────────────────────┴───────────────┘ │  │
│  │ （空态：队列为空）                            │  │
│  └────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────┤
│  #inputRow:  [^] [chatInput] [●] [Send]          │
└──────────────────────────────────────────────────┘
```

- **`^` 按钮**：`#inputRow` 内 `chatInput` 左侧；点击 toggle `#queuePanel`。队列非空时按钮加高亮/角标（`待发送 N`）。
- **面板**：普通 `div`（非浮层，vanilla 简单），位于 `#messages` 与 `#inputRow` 之间；默认折叠（`display:none`）。
- **每行截断**：CSS `white-space:nowrap; overflow:hidden; text-overflow:ellipsis` 单行；`title` 属性挂全文（hover 可读完整内容）。
- **hover 按钮**：桌面端默认 `opacity:0`，行 hover 显示（CSS 类 `:hover`）；移动端（无 hover）常显。四个按钮固定行尾：🗑 删除 / ✎ 编辑 / ↑ 上移 / ↓ 下移。
- **编辑态**：点 ✎ → 该行文本替换为 `<textarea>`（行内编辑），Enter 或 ✓ 保存、Esc 取消；保存后刷新该行显示。
- **删除**：队列项是"未发送草稿"，可重建、低风险 → **不弹 confirm**，直接删 + `toast`；首版不做撤销。
- **重排**：↑ 与上一项 swap，↓ 与下一项 swap；队首/队尾对应按钮 disabled；队列仅 1 项时 ↑↓ 均 disabled。
- **空态**：面板显示「队列为空」。
- **入队不上屏**：队列项**不调用 `addMessage('user', text)`**（避免"已发"假象与聊天记录重复）。真正 flush 发送成功后才 `addMessage('user', text)` 上屏、走后端入 history。副作用（发送瞬间消息出现）是正确语义。

### 2.5 接入点

**`send()` 改造（app.ts:1511）**：
- busy 分支（app.ts:1520）改为**入队**：
  ```ts
  if (s && (s.workerStatus === 'running' || s.workerStatus === 'held')) {
    enqueueMessage(text);   // 入队 + 清输入框 + 持久化 + 刷新面板 + toast('已加入发送队列')
    return;
  }
  ```
- 抽取内部函数 `_sendText(text): boolean`——封装原有「清输入框 + addMessage + spawn/settings 前置 + doSend」链路，`send()` 与 flush 共用。入队路径不调用 `addMessage`，flush 路径才调用。

**新增函数**（均在 app.ts）：
- `enqueueMessage(text)`：trim 校验 → 上限校验 → push `{id,text,createdAt}` → `persistQueue` → `renderQueuePanel` → 更新 `^` 角标。
- `flushQueue()`：取当前 session 队列；若 `workerStatus === 'idle'` 或有可 spawn 路径 → 取队首 `_sendText(text)`，**成功即 `shift()` 队首**并持久化、渲染；失败（WS closed / spawn 失败 / held）保留队首待下次重试。
- `renderQueuePanel()` / `toggleQueuePanel()`：面板渲染与展开。
- `removeQueued(id)` / `editQueued(id, text)` / `moveQueued(id, delta)`：操作 + 持久化 + 重渲染；`moveQueued` 到队首且 worker 空闲时触发 `flushQueue()`。
- `persistQueue(sessionId)` / `loadQueue(sessionId)`：localStorage 读写；session 切换时加载。

**复用与 hook**：
- flush 触发 hook：`_applyWorkerUpdate`（app.ts:366）——`status === 'idle'` 且队列非空 → `flushQueue()`（覆盖 `worker.result` 与 `worker.status` 两条路径）。spawn 成功回调（`/api/spawn` then 内）后也 flush（此时无 idle 事件）。
- 复用现有 `_inputDrafts` 清理、WS `open` 等待逻辑、`/api/spawn`、`/api/worker/{id}/settings` 前置。
- 与 `user_inject` 的关系：flush 逐条发送的**仍是同一 WS 消息**（`{type:'user_inject', sessionId, text}`），服务端 `send_task` 的串行队列照常工作，客户端队列只是"发送节奏的调度器 + 可视化"。

### 2.6 后端配合

- **首版：零后端改动。** 现有 `send_task` 已排队串行；客户端 flush 逐条发即可。
- 可选小增强（非必须）：`held` 时 `send_task` 返回 "Worker is held" 已走 `{type:'error'}` 广播 → 前端 toast，无需改。
- **远期（React 阶段 + 服务端队列）**：`Session` 增 `send_queue: list = field(default_factory=list)`（落盘，序列化在 session.py:242 区域）；新 API：`GET /api/sessions/{id}/queue`（读）、`POST`（入队）、`PATCH`（编辑/重排，按 index/id 操作）、`DELETE`（删单项/清空）；发送仍走 `send_task`。注意 `send_queue` 与 `pending_signal`（派发信号）、`queue_pending`（报告）职责分离，互不复用。

---

## 3. 实现步骤（vanilla 优先）

1. **数据层**：`QueuedMessage` 类型、`_queueCache`、`persistQueue`/`loadQueue`（localStorage `pan.sendQueue.<sid>`）。
2. **HTML**（改 `index.html`，实际改 `ts/app.ts` 后根目录 `npx tsc` 编译产物）：
   - `#inputRow` 内 `chatInput` 前插 `<button id="queueToggleBtn" title="发送队列">^</button>`。
   - `#messages` 与 `#inputRow` 之间插 `<div id="queuePanel" hidden>…</div>`（标题行 + 列表容器）。
3. **CSS**（`styles.css`）：面板样式、单行截断、hover 按钮显隐、编辑态 textarea、空态。
4. **`send()` 改造**：busy 分支 → `enqueueMessage`；抽 `_sendText`。
5. **flush 逻辑**：`_applyWorkerUpdate` idle hook + spawn 成功 hook + `moveQueued` 到队首即时触发。
6. **面板交互**：toggle、render、删除/编辑/重排/清空、`^` 角标。
7. **边界**：session 切换加载对应队列；队列上限 50；发送失败保留队首；held 跳过自动发送。
8. **验证**：根目录 `npx tsc --noEmit` 与 `npx tsc`（pre-commit 同样校验），手动走「worker 忙 → 入队 → idle → 自动发 → 删除/重排/编辑」全流程。
9. **React 后续**：组件化 `SendQueuePanel` + `useSendQueue`（reducer + localStorage）；若做服务端队列，封装 api.ts CRUD + 乐观更新，客户端队列退化为镜像。

---

## 4. 边界与取舍

| 边界 | 决策 |
|---|---|
| 页面刷新 / 换设备 | localStorage 本机恢复；换设备丢失（接受，远期服务端队列根治） |
| worker 空闲回收（300s）/ 重启 | 队列在客户端，不受影响；`pending_signal` 里已入队的任务丢失是现有行为，不在本设计范围 |
| `held`（takeover） | 入队允许（用户意图稍后发），但 flush 跳过 held，避免服务端硬拒刷屏 |
| 多条待发 | 自动 flush 逐条发，`result→idle` 再取下一条，不连发 |
| 发送失败（WS closed / spawn 失败） | 队首保留，下个 idle 或手动重试；沿用现有 toast |
| 队列 vs 聊天记录 | 队列项不上屏、不写 history；发送成功才 `addMessage` |
| 并发 flush 竞争 | 发出后 worker 变 `queued`/`running`，不再 idle，依赖状态机天然防重；`flushQueue` 内不循环发多条 |
| 删除确认 | 不弹 confirm（未发送草稿、低风险、可重建） |
| 队列上限 | 50 条 / session，超出拒绝并 toast |
| 服务端 `queue_pending` | 报告队列，**不复用**，避免语义混淆 |
| 服务端 `pending_signal` | 保留派发信号角色，客户端队列不直接操作它 |
