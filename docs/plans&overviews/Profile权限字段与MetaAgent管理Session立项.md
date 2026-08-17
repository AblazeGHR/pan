# Profile 权限字段 + Meta-Agent 管理被管 Session — 立项

> 状态：已实施（2026-08-17，见 `阶段计划与进度.md` P4）；其中「role 权限字段」决策已被 `Role字段取消与能力字段拆解立项.md` 取代 | 创建：2026-08-16
> 目标：给 Profile 增加权限字段；meta-agent 的 session 多出"管理的 session"；被管 session done 后把报告推入 meta-agent 消息队列，等其 idle 后消费。（其中「role 权限字段」已取消，见下方术语更新）
>
> **术语更新（2026-08-17）**：本文的 `Profile` 在 `Character概念分层重构立项.md` 中改名为 `session_template`。**`role` 字段已被取消**——拆解为能力字段（`restrict_to_managed` / `can_claim_unmanaged` / `auto_claim_created`，见 `Role字段取消与能力字段拆解立项.md`），本文 4.1 的「role 权限字段」决策被取代。本文「Profile」一律读作「session_template」。

---

## 一、需求背景

meta-agent 通过 MCP 工具（`worker_spawn` / `worker_task` / `worker_handoff` / `worker_assign` / `worker_send`）编排任意 session 完成子任务。但当前：

1. **无结构化权限**：任何 profile 创建的 session 权限边界全靠 system_prompt 约定，无字段级控制。
2. **无管理归属**：meta-agent 与它管理的 session 之间**没有持久化的父子/归属关系**——只靠 sessionId 寻址，无法枚举"我管了哪些"。
3. **无主动报告**：被管 session 完成后，结果只能被 meta-agent 轮询（`session_get`）或阻塞等待（`worker_handoff`），没有"完成后主动推报告"的机制。

---

## 二、目标

1. 给 Profile 增加 **`role` 权限字段**（单枚举，所有 profile 都可声明，meta-agent 只是取值之一，默认 `default`）。（**已废弃**：role 拆为能力字段，见顶部术语更新）
2. **内部边界**：`role` 是 Pan 内部管理边界，不透传 CLI。**后续借它改 MCP**：禁止 meta-agent 触碰不属于它的 session。
3. meta-agent 的 session 多出 **"管理的 session"** 字段（结构化归属）。
4. 被管 session **done 后**把报告推入 **meta-agent 的消息队列**，等 meta-agent **idle 后消费**（形式与 handoff 报告类似）。

---

## 三、代码事实（已调研）

### 3.1 Profile 数据结构（`packages/core/manifest_loader.py`）

`Profile` dataclass（`:40-61`）字段：`name, adapter, model, permission_mode, system_prompt, mcp_mode, mcp_servers, memory_dir, source_manifest`。**当前无独立"权限字段"**——只有 `permission_mode`（CLI 权限模式）与 `mcp_mode`。`_parse_profile`（`:144-178`）从 raw dict 解析。

### 3.2 Character 与下放（`packages/core/character.py`）

`Character` dataclass（`:39-87`）：字段与 Profile 对应，**无额外权限字段**。`create_character`（`:143-201`）：从 profile 复制 `permission_mode`/`mcp_mode`，`mcp_servers` 从名字解析成完整配置。**无权限字段下放逻辑**。

### 3.3 Session 结构（`packages/core/session.py`）

`Session` dataclass（`:27-44`）：`id, name, adapter, model, permission_mode, adapter_config, character_id, system_prompt, game_id, raw_usage, total_usage, workdir, history, last_result, created_at, updated_at`。**无 parent/owner/managed 归属字段**。

### 3.4 管理关系现状

- Session 无 parent/owner 字段（grep 确认）。
- Worker 唯一"parent"语义是 `branch_worker` 广播的 `parentWorkerId`（`worker.py:1155`，fork 血缘，非管理关系）。
- **meta-agent 与被管 session 现无持久化归属**——需新增结构字段。

### 3.5 消息队列（`packages/core/worker.py`）

- 队列：`Worker.queue` 为 `asyncio.Queue`（`:123`），入队项 `{"text","source","seq","taskId"}`。**逐个入队、FIFO 消费**（`_consumer :381` 循环 `get`）。
- `send_task`（`:1169`）：校验 worker 存活，`seq` 自增，`queue.put`，idle/queued 置 `queued` 并广播。
- **idle 判定**：`last_activity`（`_read_stdout :238`、`send_task` 刷新）；`_watchdog`（`:422`）按 idle/running 超时回收。
- **handoff 报告形状**（`worker.py:1216`）：`{"status": "done"/"error"/"pending", "result":..., "workerId", "taskId"}`。`_consumer` 完成时构造 `worker.result` 广播 + 写 `s.last_result` + `_resolve_result_waiter`。

### 3.6 watchdog 生命周期（关键约束）

`_watchdog(w)` **绑定单个 Worker**（`w._watchdog_task = create_task(_watchdog(w))`，worker.py:839/1009/1145），随 worker 生灭，`if w.worker_id not in workers: return`（`:435`）。`shutdown_all()`（服务关闭）cancel 所有 worker task（`:1336`）。

**推论**：worker 死亡时它自己的 watchdog 也一起死，**无法用 worker 级 watchdog 自愈**。要"队列非空自动恢复"必须有**服务级（全局）守护**。

### 3.7 meta-agent profile（`packages/mcp/manifest.json:27-43`）

`mcp_mode=always`、`mcp_servers=["pan"]`、`permission_mode=bypassPermissions`、`model=hy3`。

---

## 四、已定决策

### 4.1 role 权限字段（已被 `Role字段取消与能力字段拆解立项.md` 取代，见顶部术语更新）

- **命名/语义**：`role`（单字段枚举）。所有 profile 都可声明，meta-agent 只是取值之一，**默认值 `default`**。
- **内部边界**：不透传 CLI，是 Pan 内部管理边界。
- **后续 MCP 隔离**：借 `role` + 落盘的 `managed` 关系改 MCP——**禁止 meta-agent 触碰不属于它的 session**（当前只有 meta-agent 被允许用 Pan MCP）。

### 4.2 归属建模

- **双向**：
  - meta-agent session 存 **`managed`**（列表，枚举它管的 session）
  - 被管 session 存 **`managed_by`**（指向 meta-agent）
- **支持层级**：一个 session 可同时有 `managed_by` 和 `managed`（既被管又管别的）。

### 4.3 报告入队与消费（落盘真源 + 内存信号 + 拼接）

**触发点**：复用 `_consumer` 完成路径——session done 时，若它有 `managed_by`，**且 meta-agent 已订阅该 session 的报告**（见下），把报告 append 进 meta-agent 的落盘队列。

**报告订阅制（2026-08-16 补充）**：report 推送是**可选（opt-in）**，不是无条件：
- 新增 MCP 工具：`report_subscribe(session_id)` / `report_unsubscribe(session_id)`——meta-agent 通过它们管理对 managed session 的完成报告订阅
- 订阅状态存 meta-agent session（如 `Session.report_subscriptions: set[str]`，落盘）
- **触发条件**：`managed_by` 存在 **且** 目标 meta-agent 订阅了该 session → 才 append 报告；未订阅则只保留现有 `worker.result` 广播（供外部协调者用）
- **与外部监听的互斥**：`/ws/agent` + Monitor（外部协调者）与 report 订阅（meta-agent 内部）是**两套完成通知，二选一**——同用会重复通知。外部协调者监听 `worker.result` 时，meta-agent 不应再订阅报告（反之亦然）。详见 `Worker监督与事件驱动模式.md` 与 `Pan冷启动Agent编排skill立项.md`。

**格式**：对齐 handoff 报告 dict（`status/result/sessionId/taskId/workerId`），原样入队（给 AI 看，不需格式化）。

**消费模型（落盘真源 + 内存信号）**：

| 层 | 载体 | 职责 |
|----|------|------|
| **真源** | `Session.queue_pending`（落盘，`list[dict]`）| 完整消息列表，持久化，**未来编辑（改/排/拼）直接操作它** |
| **信号** | `Worker.queue`（内存，只放 `item.id`）| 唤醒 `_consumer`，不承载正文 |
| **消费** | `_consumer` | 收信号 → 从真源取一批 → 拼接 → 处理 → 删真源 |

**拼接规则**：`_consumer` 从真源取一批，**全部积压 report 拼成一条**（dict 原样 + 显眼分隔符，如 `─────`，标明来源 sessionId/workerId），作为一条消息喂给模型。**非 report 消息（handoff 任务/普通消息/system_prompt）保持单条**——handoff 需 seq 配对、system_prompt 需首条注入、普通对话需实时。

**实时性**：`_consumer` 靠内存信号唤醒，从盘拉取。消息在 MA running 时到达 → 排队（不打断 running）→ running 结束后 `_consumer` 取一批处理。拼接是 running 期间被动积累的自然批处理，不故意攒。

**层级**：被管 session 完成，报告**只发直接上级**（不层层上抛）。

**容量**：暂时无限（不设上限/丢弃）。

### 4.4 落盘与自愈

- **落盘载体**：`Session.queue_pending`（session 级字段，随 session 持久化），worker 从它拉取，**消费（发送出去）即删**。
- **落盘时机**：入队**立即写盘**；消费完删除回写。
- **防死亡丢消息**：worker 被回收/崩溃时，未消费的消息仍在落盘 `queue_pending`；**下次 spawn 时载入**，由全局 watchdog 保证"队列非空则拉起 worker"。
- **全局 watchdog（方案 A）**：服务级常驻 task（生命周期=Pan 服务），周期扫描"落盘队列非空但没有活 worker 的 meta-agent session"，自动 spawn 恢复。

### 4.5 spawn 防重复（g）

- 防重复 spawn 的防护应做在 **spawn 侧**——不只是本功能，**任何 session 都不应被重复 spawn worker**。作为通用保护落实。

### 4.6 watchdog 职责（h）

- **worker 级 watchdog 直接替换为全局级**？——待评估。若全局 watchdog 覆盖所有 worker 的 idle 回收 + 超时 + 自愈，则 worker 级可下放或合并。需在实现阶段确认是否整体替换，避免两套 watchdog 打架。

### 4.8 MA 发送消息自动加来源前缀

meta-agent 通过 `worker_send`（`packages/mcp/server.py:311`）向被管 session 发送编排消息时，**自动在 text 前拼接来源前缀**，便于目标 session 解析/区分"这条消息来自 MA 编排"vs"来自真实用户"。

**前缀格式**（定稿）：

```
////by agent : {agent_session_id} | {agent_session_title}
{text}
```

- `////` 是特殊前缀标记，供解析器/模型快速区分消息来源。
- 内容带 MA 的 sessionId + title（发件人身份）。

**实现链路（可靠，不依赖 LLM）**：

1. **Pan 服务侧注入 env**：`adapter.mcp_args()`（`packages/core/adapters/cbc/adapter.py`）写 `<workdir>/.codebuddy/mcp.json`（即 `--mcp-config` 指向的文件；注意 `<workdir>/.mcp.json` 已废弃移除，`--mcp-config` 只接受文件路径，JSON 字符串不生效，见踩坑 #4）时，给 pan server 的 entry 注入动态 `env` 字段：
   - `PAN_AGENT_SESSION_ID` = 当前 session.id（MA 的 sessionId）
   - `PAN_AGENT_SESSION_TITLE` = 当前 session.name
   
   `mcp_args()` 已透传 `env` 字段（`if "env" in srv`），cbc 启动 MCP server 子进程时会把这个 env 注入 server 进程——这正是 host 信息里可靠的部分。MCP stdio 无会话上下文、`initialize` 的 `clientInfo` 只有 name/version、继承 env 无 PAN 身份（实测 2026-08-16，cbc 不注入 MA 的 `ses_xxx`），因此只能靠 mcp.json 的 `env` 注入。
2. **MCP 工具侧拼接**：`worker_send`（server.py:311）读取这两个 env，把 `text` 改写为 `f"////by agent : {sid} | {title}\n{text}"` 再调 `/api/task`。

**注意点**：
- 仅在 `worker_send` 拼接（用户明确场景）；`worker_assign`/`worker_task` 是否也要前缀，实现时按需扩展。
- 若目标 worker 是 MCP 模式（有自己的 MCP server），前缀随消息文本进入其 history，目标模型可见。
- 该前缀与 4.3 的落盘拼接共存：目标 worker 消费时，带 `////by agent` 前缀的消息是**普通消息单条处理**（非 report 拼接），因为它是 MA 编排指令而非子任务报告。

### 4.7 handoff 演进（保留 + 标记废弃 + 队列语义澄清）

- **不删除 handoff**（保留同步返回值能力，供确需严格阻塞的场景）。
- **`Worker.queue` 职责澄清**：在"落盘真源 + 内存信号"架构下，`Worker.queue` 语义收窄为**唤醒信号**（放 `item.id`），改名以体现（如 `pending_signal`）。承载的 handoff 任务（需 seq 配对）在真源里保留 `seq/task_id`，配对逻辑不动。
- **`worker_handoff` 标记 deprecated**：推荐用 `worker_assign` + 报告消费。理由：如果确实需要等，MA 不应处于 busy 或可能被插队的状态——"等"应是 MA 的默认 idle 状态，而非一个阻塞调用动作。
- **deprecated 标记粒度**：代码注释 + MCP 工具 description（引导用 assign + 报告），暂不加运行时警告。

### 4.9 mcp-config 收敛到 Pan 内统一目录

- **问题**：`mcp_args()` 目前写 `<workdir>/.codebuddy/mcp.json`。当 workdir 在 Pan 外时（如 meta-agent session workdir=`D:/project`），Pan 会向**外部目录**写入 `.codebuddy/`——污染外部目录、且可能权限不可写。
- **决策**：收敛到 **`data/mcp-configs/<session_id>.mcp.json`**（Pan 内统一目录），`--mcp-config` 指向它，**不再写 workdir**。目录可写、可控、可清理。
- **实测（2026-08-16，cbc 2.136.0）**：
  - `--mcp-config` 指向 workdir **之外**的文件 → cbc 照常连接（工具进活跃列表、`[pan connected]`）。
  - 对照试验（workdir 内 vs 外，各跑 3-6 次共 9 次）：init 的 `mcp_servers` 时序差异**不稳定**——仅最先跑的 1 次显示 `[]`，其余 8 次直接 `connected`。偶发空是首次冷启动竞争，非位置决定；踩坑 #9 已覆盖（`[]` 不代表失败）。
- **env 注入（4.8）**：收敛后照写（文件在 Pan 内，不受影响）。
- **注意**：收敛后 workdir 内不再有 `.codebuddy/mcp.json`；session 删除时清理 `data/mcp-configs/<session_id>.mcp.json`。

---

## 五、待实现细节（决策已定，实现时确认）

1. **全局 watchdog 扫描间隔**：沿用 `_WATCHDOG_TICK_SEC` 还是独立配置？
2. **worker 级 watchdog 是否整体替换为全局级**（4.6）：涉及现有 idle 回收/超时逻辑迁移，风险需评估。
3. `managed` 列表的增删维护：被管 session 删除时清理 meta-agent 的 `managed`。
4. 报告幂等：同一 taskId 重复完成（重试）是否去重——复用现有 `_task_status`。
5. **落盘真源的写读路径**：`Session.queue_pending` 的序列化格式、并发写锁、与现有 `_sess.save_async` 的整合。
6. **信号与真源一致性**：内存信号只在"唤醒"用，`_consumer` 每次从真源头取——编辑真源后发信号即可重取，信号内容过时无碍。

---

## 六、任务拆解（若立项通过）

- [x] ~~`role` 字段~~ **废弃**：role 已取消，拆为能力字段（见 `Role字段取消与能力字段拆解立项.md`）
- [ ] 归属：`managed`（列表）+ `managed_by`（反查），双向落盘，创建/接管时写入，API 暴露，删除清理
- [ ] 报告入队（订阅制）：`_consumer` 完成路径复用，`managed_by` 存在**且 meta-agent 已订阅该 session 报告**才 append 入落盘队列，对齐 handoff 格式
- [ ] 报告订阅：`Session.report_subscriptions`（落盘）+ MCP 工具 `report_subscribe`/`report_unsubscribe`
- [ ] 落盘队列：`Session.queue_pending` 落盘（入队立即写、消费即删），spawn 时载入；`Worker.queue` 收窄为唤醒信号并改名
- [ ] 消费拼接：`_consumer` 从真源取一批，积压 report dict 原样拼接（显眼分隔 + 来源），非 report 单条
- [ ] 全局 watchdog：服务级常驻，扫描队列非空无活 worker 的 meta-agent session，自动 spawn；spawn 侧防重复
- [ ] handoff 演进：`worker_handoff` 标记 deprecated（注释 + MCP description），`Worker.queue` 改名，seq 配对不动
- [ ] MA 消息前缀：`mcp_args()` 注入 `PAN_AGENT_SESSION_ID/TITLE` env，`worker_send` 拼接 `////by agent : ...` 前缀
- [ ] mcp-config 收敛：`mcp_args()` 改写 `data/mcp-configs/<session_id>.mcp.json`（不再写 workdir），session 删除清理
- [ ] MCP 隔离（后续）：借能力字段（`restrict_to_managed` 等）+ `managed` 禁止触碰非其管理的 session
- [ ] 未来队列编辑（后续）：API 暴露真源 `queue_pending` 的查看/修改/排序/拼接，改后发信号重取
- [ ] 测试 + 文档更新（MCP 工具、SKILL.md、本立项收尾）
