# Worker 监督与事件驱动模式（实战 + 融合设想）

> 2026-08-16：协调 5 个并行 worker（fix-h1/fix-sub/fix-misc/ma-role/ma-prefix）修复 Pan 缺陷时沉淀的监督实战。
> 状态：实战记录 + 融合设想（**不改代码**）

---

## 一、问题

并行派发多个 worker 后，协调者（CodeBuddy 会话）需要**及时感知** worker 完成，才能检查提交、汇报、推进下一阶段。朴素做法是定时轮询（5 分钟粒度），但：
- 轮询延迟大（最多 5 分钟才感知）
- 每轮都查全部 session，噪音大
- worker 完成时刻与感知时刻脱节

**目标**：worker 完成 → 协调者**立即**被唤醒。

## 二、现状实战：Monitor + WS 订阅脚本

### 架构

```
Pan /ws/agent（WebSocket，worker.result 事件广播）
   ↓
订阅脚本 monitor_workers.py（websockets 连本机，订阅 worker.result）
   ↓ 每事件 print 一行
CodeBuddy Monitor 工具（command 模式 + persistent）
   ↓ 新输出唤醒协调者
协调者检查 session_get + git log → 汇报
```

### 关键实现

**脚本**（`data/scripts/monitor_workers.py`）：
```python
async with websockets.connect("ws://127.0.0.1:8768/ws/agent") as ws:
    await ws.send(json.dumps({"type": "subscribe", "eventTypes": ["worker.result"]}))
    async for msg in ws:
        ev = json.loads(msg)
        if ev.get("type") == "worker.result":
            print(f"DONE session={ev.get('sessionId')} status={ev.get('status')} worker={ev.get('workerId')}", flush=True)
```

**启动**（Monitor 工具）：
```
Monitor(command="python data/scripts/monitor_workers.py", persistent=true)
```
每次脚本输出一行 → Monitor 唤醒协调者。

### 为什么脚本中转，不直接用 Monitor 的 ws 模式

Monitor 的 `ws` 模式**拒绝连接私有/内部地址**（`127.0.0.1`/`localhost` 都被拒）——CodeBuddy 的 WebSocket 安全限制。所以用 `command` 模式跑 python 脚本，由脚本连本机 WS（无此限制），再经 stdout 中转给 Monitor。

### 优点 / 局限

| 优点 | 局限 |
|------|------|
| 事件驱动（秒级感知，替代 5 分钟轮询）| 依赖 `websockets` 库（.venv 已有 16.1.1）|
| 脚本自动重连（服务重启 5s 后恢复）| Monitor 是 CodeBuddy 侧能力，非 Pan 能力 |
| 可过滤 eventTypes/sessionIds | 脚本未入库（当前 data/scripts/ 临时位）|
| 与 cron 双保险（cron 低频兜底）| — |

## 三、泛化模板

把脚本参数化，供任意协调场景复用：

```bash
python data/scripts/monitor_worker.py \
  --sessions ses_a,ses_b \        # 只关心特定 session（默认全部）
  --events worker.result \         # 事件类型过滤（默认 worker.result）
  --format one-line                # 输出格式（供 Monitor 解析）
```

- `--sessions`：过滤关心的 worker，减少无关唤醒
- `--events`：默认 `worker.result`；可订阅 `worker.status`/`worker.spawned` 等（/ws/agent 支持）
- 输出保持**一行一事件**，兼容 Monitor 的增量输出协议

## 四、融合入 Pan 框架的设想

### 4.1 作为 MCP 工具（价值有限）

MCP 是**请求-响应式**，无法主动推送事件给调用方。worker.result 的主动感知本质只能靠 `/ws/agent`（WebSocket）。MCP 侧可补的是**查询工具**（已有 `session_get`/`worker_list`），文档说明订阅方式即可。**不推荐把监督做成 MCP 工具**。

### 4.2 作为 CodeBuddy skill（推荐轻量方案）

封装成 skill（`.codebuddy/skills/pan-worker-monitor/`）：
- 一条命令：`/pan-monitor <sessionIds...>` → 启动脚本 + 配置 Monitor
- 模板化：不用每次手写脚本/调 Monitor
- 适用：任何需要"盯梢 worker"的 CodeBuddy 协调会话

### 4.3 并入 meta-agent 报告机制（同源统一，推荐）

Profile 立项（`Profile权限字段与MetaAgent管理Session立项.md` 4.3）的 **queue_pending 报告**本质也是"subagent 完成 → 通知 MA"：
- **外部协调者**（CodeBuddy 会话）→ `/ws/agent` 事件驱动（本模式）
- **meta-agent 内部** → `queue_pending` 落盘报告 + 全局 watchdog 消费（立项中）

两者**同源**（worker.result 广播已存在），未来可统一：
- worker 完成时既有 `worker.result` 广播（供外部），也 append `queue_pending` 报告（供 meta-agent）
- 监督脚本和报告消费共用同一"完成事件"

### 4.4 watchdog 自愈联动

全局 watchdog（立项 4.4）保证"队列非空 → 拉起 worker"；本模式保证"worker 完成 → 协调者感知"。两者互补：watchdog 管执行侧，监督管感知侧。

## 五、自动化模板（落地路径）

1. **脚本入库**：`data/scripts/monitor_worker.py` 参数化后随 Pan 代码库维护（data/ 已 gitignore？需确认——若 gitignore 则放 `packages/core/scripts/` 或工具目录）
2. **skill 包装**：`/pan-monitor` 命令模板化启动
3. **文档化**：本文件 + SKILL.md 引用
4. **与 meta-agent 报告统一**：在 Profile 立项实现时，把 `/ws/agent` 事件与 `queue_pending` 报告归一到同一"完成通知"概念

## 六、待决策

1. 脚本入库位置（data/scripts/ 是否 gitignore，还是移 `packages/` 下）
2. 是否做 skill 包装（`/pan-monitor`）
3. 是否在 Profile 立项里把"完成通知"统一建模（/ws/agent 事件 + queue_pending 报告双通道）

---

## 关联文档

- `docs/plans&overviews/Profile权限字段与MetaAgent管理Session立项.md` — queue_pending 报告机制（4.3）
- `docs/cbc-mcp-踩坑记录.md` — /ws/agent 订阅协议（事件过滤、consumed_seq）
