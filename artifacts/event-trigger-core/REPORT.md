# Event–Trigger–Action 第一阶段候选交付

工作树：`D:/project/pan-worktrees/event-trigger-core-20261022`
分支：`feature/event-trigger-core-20261022`
基线：`05a91528f5b58467976ffaa3122babeb5a497a68`
候选提交：以本报告所在分支 `git log -1 --format=%H` 为准；本报告随代码一起提交。

## 实现与交接

已实现 EventDefinition registry、可信运行时 handle、有界异步独立扇出、
Trigger/Run 原子持久化及 revision 校验、State 比较/布尔求值与单次字段缓存、
literal/Event 参数冻结、Event+Trigger 去重、逐 Trigger 串行和跨 Trigger 独立执行。
Run 先落盘，再通过持久 admission fence 开始执行。关闭取消 queued、running 继续；
显式取消需要 token 和 Run revision；失败不重试；中断 running 恢复为 uncertain。
插件定义不可用时队列等待，重新 ready 后恢复；满载和持久化失败有可见诊断。

Action 开始时按 ID 读取最新源定义，并保存定义和摘要，不依赖 Group/build。
API/命令沿用共用执行基础，新增 Session Operation 的 explicit/event 选择器，
固定去重目标、逐目标 checkpoint 和聚合结果。Pause 调用原
`api_session_queue_paused` 业务函数，沿用真实锁、持久状态及队列 gate。
Group immutable release/pin 没有改变；旧 build/provider 不接受新增的自动化
Session Operation 类型，保留原执行安全限制。

用户本地 key 换取 12h 进程内 Bearer；启停、删除、已启用配置修改和取消 Run
校验 token；Agent 只能维护 disabled 草稿。token 注销/过期不重新鉴权自动执行。
注册和发布没有公共 HTTP 入口，source 由可信运行时 handle 绑定。
内部示例插件验证注册/发布/订阅/停用撤销资格。
Worker 事件直接观察原 Worker.status 和原 registry，保存的 previous 仅用于边沿
比较，不是另一份权威计数。正数归零发布，零到零不发布。

接口/数据/容量/插件适配器和后续前端交接见
[核心契约](../../docs/design/Event-Trigger-Action-v0.1-core-contract.md)。
两份原始未提交的权威设计一并纳入本候选。

## 冻结代码验证

命令（退出码记录于 `exit-codes.json`）：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_automation_core.py tests/test_automation_http.py tests/test_action_groups.py tests/test_action_ids.py tests/test_session_queue_api.py -o addopts= -q --junitxml=artifacts/event-trigger-core/final.xml
python -m compileall -q packages/automation packages/actions packages/core/worker.py packages/web/server.py tests/test_automation_core.py tests/test_automation_http.py tests/support/automation_http_server.py
git diff --check
```

- 最终定向测试：**112 passed，0 failed，0 errors，0 skipped，退出码 0**。
- compileall：退出码 0；diff check：退出码 0。
- 新核心 20 项、真实 HTTP 1 项，其余为原 Action/Group/ID/queue 定向回归。
- 5 条 warnings：当前解释器没有 pytest-timeout，已有 timeout 配置/标记未生效；
  另有现有 FastAPI TestClient 的 httpx 弃用提示。没有通过新增 skip 或弱化断言处理。
  新测试使用 asyncio.run 及显式有界等待，HTTP harness 自带启动/请求/清理超时。
- 证据：[final.log](final.log)、[final.xml](final.xml)、[退出码](exit-codes.json)。

失败证据保留：`core-initial-failed.log/xml`（缺 pytest-asyncio，新测试改用仓库
现有 asyncio.run 方式），`http-initial-failed.log`、`http-fixture-failed.log`、
`http-shape-failed.log`（最小空插件配置需显式空 mcpServers，以及复用旧测试助手时
Session/queue 回包字段对齐）。均为退出码 1，未删除断言绕过；修正后最终组合通过。
较早单项及组合日志作为过程证据保留，不与最终 112 项叠加计数。
提交的文本日志仅统一换行并去除行尾空格，保留错误与断言正文。

## 真实隔离 HTTP / fakeCLI 证据

最终实例：端口 **64477**，PID **9032**；完整命令、createTime、临时根、Event/Run/
目标结果/queue 快照及 fakeCLI 收到的消息见 [http-evidence.json](http-evidence.json)。
临时 profile 位于该证据记录的 pytest temp 目录，代码从本工作树加载。

实测步骤与结果：

1. 三个自有 Session 使用确定性本地 stream-json fakeCLI；没有调用收费 provider。
2. Trigger 默认关闭时 source 完成任务，不创建 Run，不暂停目标。
3. 目标持有一条 locked 待处理消息；用户登录启用 Trigger，随后注销 Bearer。
4. source 的真实 running -> idle 产生 workers.all_idle；持久 Run 成功并记录目标。
5. 原 Pause 业务将 `queue_paused=true` 落入目标 Session 文件。
6. 解锁目标消息后，它仍留在队列，fakeCLI 没有收到该消息；对照 Session 的消息
   正常交付。此测试通过准备 locked backlog 避免声称零时延阻止已先行交接的消息。
7. 污染父环境指向的模拟实用目录 sentinel bytes 和文件清单保持不变。
8. 已核验自有 PID/createTime 并清理；`ownedProcessesRemaining=[]`，
   `portReleased=true`。不依赖真实 data，不访问 8767/8768。

自动化边界：这是实际 loopback HTTP、真实业务持久化、真实 Worker/fakeCLI 和队列
交接验证；不是收费 provider 消费证明，也不是真人 UI/队列验收。

## 偏差、限制和后续必做

- 本阶段未实现生产前端、完整外部插件传输或其 catalog 生命周期自动绑定；
  后续 adapter 应调用已有 Service runtime hooks，不另造插件启用权威。
- `query` 选择器明确为**必做后续**，已保留类型位置，当前运行时拒绝；parallel
  亦仅保留扩展类型。条件 false 额外持久化为正常终态 skipped，便于观察。
- API/命令能力没有扩成所有 Pan API 适配；现有受限 GET/命令路径仍可动态调用，
  新业务示例由显式内部适配完成。其他 API 后续逐项保留业务检查适配。
- 单文件 JSON ledger 会随历史增长而重写，尚无大规模/长时间负载验收；
  持久历史保留、分页和索引需要后续设计。当前磁盘操作在工作线程，事务锁不跨
  Action/provider/目标操作；慢盘验证证明 event loop 可继续运行。
- Event 发布到 Run 持久化前仍有丢失窗口；bus 满载是可见拒绝，不承诺可靠重投。
  持久化前诊断仅保留在进程内有界环中。admission fence 写盘失败时已落盘 queued
  保留等待，重启恢复可执行；未提供自动重试或 exactly-once 承诺。
- 本轮恢复是确定性核心账本恢复/重建验证；没有宣称 OS 强杀全过程故障注入或
  真人现场恢复验收。最终 HTTP 证明真实业务闭环，真人验收仍待 MA 安排。
- 新增 session_operation 仅由内部自动化执行；旧 Group 发布和旧 provider 入口
  明确拒绝它。后续若需通过 Group 暴露写操作，需要单独明确授权语义。

未跑本地全量、未写共享依赖、未启动真实 CBC/model、未移动 main/practical、
未 push/release、未重启或操作现有服务，未派子任务。候选完成后停在本阶段，
由 MA 独立验收；本报告不表示 MA 已验收或已合并。
