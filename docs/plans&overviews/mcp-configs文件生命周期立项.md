# mcp-configs 文件生命周期 — 立项（先调查后决定）

> 背景：`data/mcp-configs/<session_id>.mcp.json` 是 worker 的 MCP 配置（`--mcp-config` 指向），目前每次 spawn 覆盖写、**从不清理**（泄漏）。需要调查：能否"写临时文件、用后即删"，还是 worker 运行期间必须存在。
> 状态：立项调查阶段（**不改代码**）| 创建：2026-08-17

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

## 四、候选策略（调查后决定）

| 策略 | 说明 | 适用 |
|------|------|------|
| **S1 spawn 后立即删** | mcp_args 写完文件→spawn 进程→删除 | 仅实验 A/B/C 全通过 |
| **S2 worker 退出时删** | 文件绑定 worker 生命周期（spawn 写、kill/退出删）| 实验 B 失败但 A 通过 |
| **S3 session 删除时清** | 现状缺口补齐（文件保留到 session 删除）| 保守默认 |

**组合**：S2 + S3（worker 退出即删 + session 删除兜底清理残留）是安全且干净的中间态。

## 五、待决策

1. 调查结果属于哪种读取时机？
2. 采用 S1（激进）/ S2+S3（推荐）/ S3（保守）？
3. 失败时是否回退重写文件（重试逻辑）？

## 六、任务拆解（若立项通过）

- [ ] 实验 A/B/C（mcp_args 写文件 + spawn + 删 + handoff 验证）
- [ ] 按结果选择策略，实现清理逻辑（adapter.py mcp_args + worker kill/退出路径 + session 删除钩子）
- [ ] 测试：文件生命周期、残留清理、resume 场景
- [ ] 更新 `Worker监督与事件驱动模式.md` / 相关文档（文件生命周期约定）

---

## 关联文档

- `docs/cbc-mcp-踩坑记录.md` — `--mcp-config` 只接受文件路径（JSON 字符串不生效，踩坑 #4）
- `docs/plans&overviews/Profile权限字段与MetaAgent管理Session立项.md` — 4.9 mcp-config 收敛
- `docs/plans&overviews/MCP启用单一事实源收敛立项.md` — 相邻的 MCP 配置收敛
