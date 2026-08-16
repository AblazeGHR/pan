# Profile 权限字段 + Meta-Agent 管理被管 Session — 立项

> 状态：立项阶段（决策已定，**不改代码**） | 创建：2026-08-16
> 目标：给 Profile 增加权限字段；meta-agent 的 session 多出"管理的 session"；被管 session done 后把报告推入 meta-agent 消息队列，等其 idle 后消费。

---

## 一、需求背景

meta-agent 通过 MCP 工具（`worker_spawn` / `worker_task` / `worker_handoff` / `worker_assign` / `worker_send`）编排任意 session 完成子任务。但当前：

1. **无结构化权限**：任何 profile 创建的 session 权限边界全靠 system_prompt 约定，无字段级控制。
2. **无管理归属**：meta-agent 与它管理的 session 之间**没有持久化的父子/归属关系**——只靠 sessionId 寻址，无法枚举"我管了哪些"。
3. **无主动报告**：被管 session 完成后，结果只能被 meta-agent 轮询（`session_get`）或阻塞等待（`worker_handoff`），没有"完成后主动推报告"的机制。

---

## 二、目标

1. 给 Profile 增加 **`role` 权限字段**（单枚举，所有 profile 都可声明，meta-agent 只是取值之一，默认 `default`）。
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
- Worker 唯一"parent"语义是 `branch_worker` 广播的 `parentWorkerId`（`worker.py:1117`，fork 血缘，非管理关系）。
- **meta-agent 与被管 session 现无持久化归属**——需新增结构字段。

### 3.5 消息队列（`packages/core/worker.py`）

- 队列：`Worker.queue` 为 `asyncio.Queue`（`:123`），入队项 `{"text","source","seq","taskId"}`（`:1150`）。**逐个入队、FIFO 消费**（`_consumer :360-393` 循环 `get`）。
- `send_task`（`:1131-1161`）：校验 worker 存活，`seq` 自增，`queue.put`，idle/queued 置 `queued` 并广播。
- **idle 判定**：`last_activity`（`_read_stdout :229`、`send_task :1149` 刷新）；`_watchdog`（`:399-444`）按 idle/running 超时回收。
- **handoff 报告形状**（`worker.py:1178-1232`）：`{"status": "done"/"error"/"pending", "result":..., "workerId", "taskId"}`。`_consumer` 完成时构造 `worker.result` 广播 + 写 `s.last_result` + `_resolve_result_waiter`。

### 3.6 watchdog 生命周期（关键约束）

`_watchdog(w)` **绑定单个 Worker**（`w._watchdog_task = create_task(_watchdog(w))`，worker.py:804/971/1107），随 worker 生灭，`if w.worker_id not in workers: return`（`:412-413`）。`shutdown_all()`（服务关闭）cancel 所有 worker task（`:1308-1313`）。

**推论**：worker 死亡时它自己的 watchdog 也一起死，**无法用 worker 级 watchdog 自愈**。要"队列非空自动恢复"必须有**服务级（全局）守护**。

### 3.7 meta-agent profile（`packages/mcp/manifest.json:27-43`）

`mcp_mode=always`、`mcp_servers=["pan"]`、`permission_mode=bypassPermissions`、`model=hy3`。

---

## 四、已定决策

### 4.1 role 权限字段

- **命名/语义**：`role`（单字段枚举）。所有 profile 都可声明，meta-agent 只是取值之一，**默认值 `default`**。
- **内部边界**：不透传 CLI，是 Pan 内部管理边界。
- **后续 MCP 隔离**：借 `role` + 落盘的 `managed` 关系改 MCP——**禁止 meta-agent 触碰不属于它的 session**（当前只有 meta-agent 被允许用 Pan MCP）。

### 4.2 归属建模

- **双向**：
  - meta-agent session 存 **`managed`**（列表，枚举它管的 session）
  - 被管 session 存 **`managed_by`**（指向 meta-agent）
- **支持层级**：一个 session 可同时有 `managed_by` 和 `managed`（既被管又管别的）。

### 4.3 报告入队与消费（落盘真源 + 内存信号 + 拼接）

**触发点**：复用 `_consumer` 完成路径——session done 时，若它有 `managed_by`，把报告 append 进 meta-agent 的落盘队列。

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

### 4.7 handoff 演进（保留 + 标记废弃 + 队列语义澄清）

- **不删除 handoff**（保留同步返回值能力，供确需严格阻塞的场景）。
- **`Worker.queue` 职责澄清**：在"落盘真源 + 内存信号"架构下，`Worker.queue` 语义收窄为**唤醒信号**（放 `item.id`），改名以体现（如 `pending_signal`）。承载的 handoff 任务（需 seq 配对）在真源里保留 `seq/task_id`，配对逻辑不动。
- **`worker_handoff` 标记 deprecated**：推荐用 `worker_assign` + 报告消费。理由：如果确实需要等，MA 不应处于 busy 或可能被插队的状态——"等"应是 MA 的默认 idle 状态，而非一个阻塞调用动作。
- **deprecated 标记粒度**：代码注释 + MCP 工具 description（引导用 assign + 报告），暂不加运行时警告。

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

- [ ] `role` 字段：Profile / Character / Session + manifest 声明 + 下放（manifest_loader / character / session / server 透传），默认 `default`
- [ ] 归属：`managed`（列表）+ `managed_by`（反查），双向落盘，创建/接管时写入，API 暴露，删除清理
- [ ] 报告入队：`_consumer` 完成路径复用，`managed_by` 存在则 append 报告入 meta-agent 落盘队列，对齐 handoff 格式
- [ ] 落盘队列：`Session.queue_pending` 落盘（入队立即写、消费即删），spawn 时载入；`Worker.queue` 收窄为唤醒信号并改名
- [ ] 消费拼接：`_consumer` 从真源取一批，积压 report dict 原样拼接（显眼分隔 + 来源），非 report 单条
- [ ] 全局 watchdog：服务级常驻，扫描队列非空无活 worker 的 meta-agent session，自动 spawn；spawn 侧防重复
- [ ] handoff 演进：`worker_handoff` 标记 deprecated（注释 + MCP description），`Worker.queue` 改名，seq 配对不动
- [ ] MCP 隔离（后续）：借 `role` + `managed` 禁止 meta-agent 触碰非其管理的 session
- [ ] 未来队列编辑（后续）：API 暴露真源 `queue_pending` 的查看/修改/排序/拼接，改后发信号重取
- [ ] 测试 + 文档更新（MCP 工具、SKILL.md、本立项收尾）
