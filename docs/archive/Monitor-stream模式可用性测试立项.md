# Monitor 在 stream 模式下的可用性测试 — 立项

> 背景：Monitor 是 CodeBuddy（cbc）的后台监听工具，需验证其在 **stream/非交互模式**（`-p --output-format stream-json`）下是否可用。
> 状态：已测试（2026-08-16，结论见第四节） | 创建：2026-08-16

---

## 一、背景与动机

`Worker监督与事件驱动模式.md` 用 Monitor（command 模式）接收 worker 完成信号，但 Monitor 是 **cbc 会话侧能力**：

- 交互式界面（默认 TUI）下已实测可用（2026-08-16 协调 5 个 worker 时）
- **未验证 stream/非交互模式**（`-p --output-format stream-json`，即 Pan worker 的 cbc 进程形态）下是否可用

**为什么重要**：若 Monitor 只在交互式可用，则：
- "外部协调者用 Monitor 盯梢"仅限**交互式 CodeBuddy 会话**（如我们现在的会话）
- Pan worker 进程（stream/one-shot cbc）**内部无法用 Monitor** → worker 完成通知只能靠 `/ws/agent` 或报告订阅
- 影响 `Pan冷启动Agent编排skill立项` 中 skill 对监督模式的描述（是否推荐 Monitor）

## 二、待验证点

| # | 验证点 | 判定 |
|---|--------|------|
| 1 | cbc `-p --output-format stream-json` 模式下，Monitor 工具是否注册/出现在工具列表 | 有 / 无 |
| 2 | stream 模式下能否调用 Monitor（command/ws 模式）| 成功 / 报错 |
| 3 | stream 模式下 Monitor 的事件驱动唤醒是否工作（空闲唤醒语义）| 工作 / 不工作 |
| 4 | 若不可用：报错信息与原因（工具未注册 / 依赖交互会话 / 其他）| 记录 |
| 5 | 与 Pan worker 场景关联：Pan worker 的 cbc 进程能否用 Monitor 盯梢子 worker | 能 / 不能 |

## 三、测试方法（初拟）

1. **stream 模式探测**：cbc `-p --output-format stream-json -y` 下发 prompt，让 agent 回答"你有哪些后台监听/监控类工具"——看 Monitor 是否在工具列表
2. **直接调用探测**：stream 模式下让 agent 尝试调用 Monitor（command 模式跑 `monitor_workers.py` 或一个临时脚本），观察是否成功/报错
3. **事件驱动验证**（若可用）：stream 模式下 Monitor 跑脚本输出，观察 agent 是否被唤醒处理
4. **对照**：同一命令在交互式（无 `-p`）下的行为，确认差异来源是 `-p`

## 四、结论意义（测试后更新）

**测试日期：2026-08-16** | 环境：cbc v2.137.0，`cbc -p --output-format stream-json -y`（glm-5.3）

**结论：Monitor 在 stream/非交互模式（`-p --output-format stream-json`）下完全可用。**

| # | 验证点 | 结果 | 证据 |
|---|--------|------|------|
| 1 | stream 模式工具注册 | **有** | ① 本进程（stream/one-shot cbc）deferred tools 含 Monitor，ToolSearch 可加载完整 schema（command/ws、timeout_ms、persistent）；② `cbc -p --output-format stream-json` 探测的 init 事件 `tools` 数组含 `"Monitor"`（另含 CronCreate/CronDelete/CronList/TaskOutput/TaskStop/PushNotification/Workflow）。注：`cbc --help` 无 `--monitor` 参数/子命令——Monitor 是**工具**而非 CLI 选项 |
| 2 | stream 模式调用 Monitor（command 模式） | **成功，无报错** | 两次独立探测 `DeferExecuteTool({toolName:"Monitor", command:"bash echo_probe.sh"})` 均 `is_error:false`，返回 `Monitoring in background with task_id` + `<monitor-event>` 投递说明；本进程同样调用成功 |
| 3 | 事件驱动唤醒 | **工作（进程存活期内）** | ① 探测：agent 设置 Monitor 后继续执行 Bash，收到全部 4 行增量输出（0 丢失），事件在轮次边界批量送达；② 本进程实测：收到 4 个 `<monitor-event>`（3 行输出 + done 状态）并被打断继续处理 |
| 4 | 报错信息 | 无报错 | 未出现"工具未注册 / 依赖交互会话"类错误 |
| 5 | Pan worker 场景 | **能用（有前提）** | worker（one-shot cbc）在 Monitor 监听期间只要有**后续轮次**，事件即会在轮次边界送达 |

**与交互式的差异**：
- 工具注册、调用、事件投递语义**完全一致**（同为"busy 时轮次边界投递 / idle 时唤醒"）。
- 唯一差异来自 **one-shot 进程生命周期**：`-p` 进程在 agent 给出最终回答后立即退出，退出后到达的输出**无接收方**。实测：agent 设置 Monitor 后立即 finalize，后续 4 行输出全部丢失（probe1）；而保持后续动作则 4 行全部收到（probe2）。交互式会话常驻，无此问题。
- 因此 worker 进程内部用 Monitor 盯梢子 worker 时，**必须保证设置 Monitor 后仍有后续动作**；更稳妥的是由外部协调者（常驻进程）用 Monitor 盯梢。

**对文档的影响**：`Worker监督与事件驱动模式.md` 与 `Pan冷启动Agent编排skill立项.md` **维持现状，无需改写**——Monitor 同样适用于无头/自动化协调（stream 形态 worker 亦可，注明上述生命周期前提即可）。

## 五、任务拆解

- [x] 测试：stream 模式工具列表探测（#1）— 2026-08-16，工具已注册
- [x] 测试：stream 模式 Monitor 调用（#2/#3）— 2026-08-16，调用成功、事件投递工作（进程存活期内）
- [x] 测试：交互式对照（#4）— 2026-08-16，语义一致，仅 one-shot 进程生命周期差异
- [x] 根据结果更新监督文档 + skill 立项（#5）— 结论：支持，文档维持现状

---

## 关联文档

- `docs/plans&overviews/Worker监督与事件驱动模式.md` — Monitor 实战与完成通知二选一
- `docs/plans&overviews/Pan冷启动Agent编排skill立项.md` — skill 对监督模式的描述
- `docs/pan-test-分支质量审查-2026-08-15.md` — /ws/agent 订阅协议
