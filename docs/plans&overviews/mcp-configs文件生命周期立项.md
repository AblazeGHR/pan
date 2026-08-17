# mcp-configs 文件生命周期 — 立项（先调查后决定）

> 背景：`data/mcp-configs/<session_id>.mcp.json` 是 worker 的 MCP 配置（`--mcp-config` 指向），目前每次 spawn 覆盖写、**从不清理**（泄漏）。需要调查：能否"写临时文件、用后即删"，还是 worker 运行期间必须存在。
> 状态：**调查完成（2026-08-17）**——读取时机已定、策略已选（S3，仅 session 删除后清理），实现完成 2026-08-17 | 创建：2026-08-17

---

## 一、现状

- **写入**：`packages/core/adapters/cbc/adapter.py` `mcp_args()` 每次 spawn 写 `data/mcp-configs/<session_id>.mcp.json`（覆盖写，含 pan server entry + env 注入）
- **引用**：cbc 经 `--mcp-config <文件>` 启动，文件内容是 MCP server 配置（command/args/env）
- **清理**：**无**——文件在 `data/mcp-configs/` 永久残留（ma-prefix 已记录"session 删除时清理"未纳入）
- **多 worker**：同一 session 重 spawn 会覆盖写同一文件

## 二、调查问题（核心）

**cbc（CodeBuddy CLI）对 mcp-config 文件的读取时机：**
1. 启动时一次性读取（初始化 MCP 连接后文件不再需要）？
2. 还是运行期间持续读取（重连/工具懒加载/子进程延迟访问）？

**决定文件能否"用后即删"的关键：**
- 若**启动读一次** → 文件可在 spawn 后立即删（或 worker 退出时删）
- 若**运行期间需读** → 文件必须存活到 worker 退出

## 三、调查方法（实验）

### 实验 A：spawn 后立即删
1. 创建 MCP session（meta-agent），spawn worker
2. **立即删除** `data/mcp-configs/<id>.mcp.json`
3. handoff 调 MCP 工具（`session_list`）
4. 观察：MCP 工具是否正常？报错信息是什么？

### 实验 B：worker 运行中删
1. 同上，但先 handoff 一次（确认 MCP 正常）
2. 运行中删除文件
3. 再 handoff 调 MCP 工具
4. 观察：是否受影响？

### 实验 C：resume 场景
1. worker 完成后保持 cli_session_id
2. 重 spawn（resume），文件已删
3. handoff 调 MCP 工具
4. 观察：resume 是否重新读文件？

**判定**：
- A/B/C 都正常 → cbc 启动读一次，文件可"spawn 后即删"
- B 失败 → 文件须存活到 worker 退出
- C 失败 → resume 前须重写文件（或删前确认无 resume）

## 四、调查结果（2026-08-17）

> 实测环境：pan-test 分支服务（8767），HTTP API 操作 + 直接删文件。
> 测试会话 `ses_a3e0e32bb01382ed`（mcp_servers=["pan"]，stream+MCP 模式，worker-2）。
> 判定依据：handoff 要求 worker 直接调用 `mcp__pan__session_list` 并报告结果。

### 实验 A：spawn 后立即删 → **MCP 工具不可用**

1. spawn（worker-2）→ `data/mcp-configs/ses_a3e0e32bb01382ed.mcp.json` 立即写出（内容含 pan server entry + env 注入，确认文件存在）
2. `rm` 删除该文件
3. handoff 调 `mcp__pan__session_list` → **失败**：
   - ToolSearch 精确名 `mcp__pan__session_list` 无匹配
   - `WaitForMcpServers(["pan"])` 返回 `ready: false`，"pan MCP 服务器未配置/已断开"
   - 结论：**文件不存在 → MCP 工具不可用**

### 实验 B：worker 运行中删 → **决定性证据：运行期间持续读取**

| 步骤 | 文件状态 | handoff 调 MCP 工具 | 结果 |
|------|---------|--------------------|------|
| B1 | 文件存在（respawn 后重写） | `mcp__pan__session_list` | ✅ 成功，返回 4 个会话 |
| B2 | **运行中删除文件** | `mcp__pan__session_list` | ❌ 工具不可用："pan MCP 服务器已掉线" |
| B3 | **不重启 worker，仅重建文件** | `mcp__pan__session_list` | ✅ 恢复，返回 4 个会话 |

- **B2→B3 是铁证**：同一 worker 进程（worker-2）、未 respawn、未重启 cbc，仅"删文件/重建文件"切换就让 MCP 工具 可用↔不可用 来回切换。
- 判定：**cbc 在运行期间会重新读取 mcp-config 文件**（至少每次任务/重连时），不是"启动读一次"。

### 实验 C：resume 场景 → **spawn 必重写文件，resume 不受影响**

1. 保留 cliSessionId（`5f9fb383-...`），worker 处于 idle
2. 重 spawn（`POST /api/spawn`，走 `--resume`）→ `mcp_args()` 在 spawn 时**重新写入**文件（即使删过也重建）
3. handoff 调 `mcp__pan__session_list` → ✅ 成功，返回 4 个会话

- 判定：**spawn 每次都重新写文件**，resume 复用新文件；文件删除只影响**当前存活 worker 的下一次任务**。

### 读取时机判定（核心问题答案）

**cbc 是"运行期间持续/按需读取"，不是"启动读一次"。** 证据链：
- 启动后立即删 → MCP 不可用（实验 A）
- 运行中删 → 下一次 handoff 即失效（实验 B2）
- 不重启重建文件 → 恢复可用（实验 B3）
- 文件只在 worker 进程存活期间被需要；worker 退出后文件即死文件，resume/重 spawn 会重建

### 推荐策略

**S3（仅 session 删除后清理），否决 S1（spawn 后立即删）与 S2（worker 退出时删）——用户 2026-08-17 决策：不做 worker 退出删除，只做 session 删除后删除。**

| 策略 | 判定 | 说明 |
|------|------|------|
| S1 spawn 后立即删 | ❌ 否决 | 实验 A/B 均证明运行期间需要文件，删即断 MCP |
| S2 worker 退出时删 | ✅ 采纳 | 文件绑定 worker 生命周期：spawn 写、kill/退出删；worker 退出后文件不再被读，可安全删 |
| S3 session 删除时清 | ✅ 兜底 | 覆盖 worker 异常崩溃未走到退出清理的残留（现状缺口补齐） |

- 实现落点：`worker.py` kill_worker / 进程退出路径（`_read_stdout` EOF → 清理 mcp-config）、`server.py` session 删除钩子。
- 注意：同一 session 多次 spawn 是覆盖写同一文件，S2 删除时机以 worker 生命周期为准（见"多 worker"现状）。

### 其他观察

- 本次调查会话 `ses_f063046e8f5054eb` 本身 `adapter_config.mcp_servers` 为空 → `mcp_args()` 返回 `[]`，**从未写过 mcp-config 文件**。即文件只在配置了 mcp_servers 的 session 上产生。

## 五、候选策略（调查后决定）

| 策略 | 说明 | 适用 |
|------|------|------|
| **S1 spawn 后立即删** | mcp_args 写完文件→spawn 进程→删除 | 仅实验 A/B/C 全通过 |
| **S2 worker 退出时删** | 文件绑定 worker 生命周期（spawn 写、kill/退出删）| 实验 B 失败但 A 通过 |
| **S3 session 删除时清** | 现状缺口补齐（文件保留到 session 删除）| 保守默认 |

**选定：S3**（session 删除时清理 data/mcp-configs/<id>.mcp.json）——worker 运行期间文件保持（cbc 持续读取），session 删除后一并清理。

## 六、待决策

1. ~~调查结果属于哪种读取时机？~~ → **已答（四）：运行期间持续读取**
2. ~~采用 S1（激进）/ S2+S3（推荐）/ S3（保守）？~~ → **已答（2026-08-17）：S3**
3. 失败时是否回退重写文件（重试逻辑）？

## 七、任务拆解（若立项通过）

- [x] 实验 A/B/C（mcp_args 写文件 + spawn + 删 + handoff 验证）— 已完成 2026-08-17，见四
- [x] 实现清理逻辑（session 删除钩子 _cleanup_mcp_config，server.py）— 完成 2026-08-17，策略 S3
- [ ] 测试：文件生命周期、残留清理、resume 场景
- [ ] 更新 `Worker监督与事件驱动模式.md` / 相关文档（文件生命周期约定）

---

## 关联文档

- `docs/cbc-mcp-踩坑记录.md` — `--mcp-config` 只接受文件路径（JSON 字符串不生效，踩坑 #4）
- `docs/plans&overviews/Profile权限字段与MetaAgent管理Session立项.md` — 4.9 mcp-config 收敛
- `docs/plans&overviews/MCP启用单一事实源收敛立项.md` — 相邻的 MCP 配置收敛
