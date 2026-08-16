# Monitor 在 stream 模式下的可用性测试 — 立项

> 背景：Monitor 是 CodeBuddy（cbc）的后台监听工具，需验证其在 **stream/非交互模式**（`-p --output-format stream-json`）下是否可用。
> 状态：立项阶段（仅记录测试计划，**不改代码**） | 创建：2026-08-16

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

- **若 stream 模式支持 Monitor**：技能文档维持现状，Monitor 可用于无头/自动化协调
- **若不支持**：更新 `Worker监督与事件驱动模式.md` 与 `Pan冷启动Agent编排skill立项.md`——明确 **Monitor 仅限交互式协调会话**，无头场景的完成通知走 `/ws/agent`（外部）或报告订阅（内部），skill 手册写明适用范围

## 五、任务拆解

- [ ] 测试：stream 模式工具列表探测（#1）
- [ ] 测试：stream 模式 Monitor 调用（#2/#3）
- [ ] 测试：交互式对照（#4）
- [ ] 根据结果更新监督文档 + skill 立项（#5）

---

## 关联文档

- `docs/plans&overviews/Worker监督与事件驱动模式.md` — Monitor 实战与完成通知二选一
- `docs/plans&overviews/Pan冷启动Agent编排skill立项.md` — skill 对监督模式的描述
- `docs/pan-test-分支质量审查-2026-08-15.md` — /ws/agent 订阅协议
