# Session query selector 后端候选交付

工作树 `D:/project/pan-worktrees/event-trigger-core-20261022`，分支
`feature/event-trigger-core-20261022`；开始时 clean，基线为 MA 已接受的
`f8bcf34cfc991230c38267712e3c658ae60cbf54`。

**精确受测实现 SHA：`4dd87d660c594267d63275ea15a1e78f85a41790`**。
本报告所在后继提交仅增加验证证据，未修改该 SHA 的产品/测试源码。
当前候选 SHA 请由 `git log -1 --format=%H` 核验。

## 实现与前端交接

Action `execution.selector` 新增 `{mode:"query",query:Predicate}`。
Predicate 为 and/or/not 或严格 field/op/value 叶子。七字段均可可靠取得：
id/name/adapter 字符串，managedBy 字符串或 null，readonlySession/pinned 布尔，
workspaceIds 字符串数组。pinned 来自共享 Pin；workspaceIds 来自现有 managed-root
有效工作区，不读取已过时子节点原始工作区。无需删去字段。

精确 typed eq/ne；标量 in 为非空同类型列表；workspaceIds.contains 为精确元素，
name.contains 为非空、区分大小写的字面 Unicode 子串。禁止脚本/SQL/正则/任意
属性遍历，拒绝空布尔组、未知字段/键/操作符、错误类型，不隐式选择全体。
限制深度8（根1）、节点64、操作数数组128、文字512 code points、候选2048、
去重结果256；超限整次失败，不截断。极深谓词在 Action/draft deepcopy 前拒绝。

执行开始一次 off-loop 浅元数据投影，目标 ID 去重升序，并在首次副作用前与
selection(mode/matchedAt/candidateCount/matched) 一起落盘。冻结后改名/移动/
新增不重算；逐目标业务检查、部分失败、取消/uncertain 沿用原机制。
恢复 running -> uncertain 不重新查询或重放；恢复 queued 在首次真正执行时查询。
零结果成功，并保存空 targetIds、matched=0 及选择诊断。取消查询会等待自有读线程
退场，避免遗留元数据加载工作。

`GET /api/actions/session-operation-template` 返回 disabled 普通 Action 模板、
字段类型、递归 JSON Schema 和限制，不返回 Session、不执行查询。现有 source/
draft API 接受 query；Agent 草稿仍强制 disabled，用户启用 Trigger token 门禁
不变。query 不创建额外 Agent Session 查询工具、身份上下文或权限覆盖字段；
选择条件不构成授权。旧 Group build/provider 拒绝 Session Operation，immutable
release/pin 不变。已有 Pan 本地 HTTP 信任边界没有升级为 per-user 安全隔离。

前端与后续并行契约见
[core-contract](../../docs/design/Event-Trigger-Action-v0.1-core-contract.md)。
两份设计、Actions 文档、Pan skill/HTTP 引用及 action_drafts 工具描述均已同步。
没有修改 React、外部插件适配或其他 TA 工作树。

## 受测命令与证据

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_session_query.py tests/test_automation_core.py tests/test_automation_concurrency.py tests/test_automation_http.py tests/test_action_groups.py tests/test_action_ids.py tests/test_session_queue_api.py tests/test_session_metadata_api.py tests/test_session_pin_api.py tests/test_workspaces_backend.py -o addopts= -q --junitxml=artifacts/session-query/final.xml
python -m compileall -q packages/actions packages/automation/contracts.py packages/core/session.py packages/mcp/server.py tests/test_session_query.py tests/test_automation_http.py
git diff --cached --check
```

- 最终 **192 passed，0 failures，0 errors，0 skipped；退出码0**。
- query 专项47项，含闭环 HTTP；另保留 explicit HTTP 和原核心/并发/Action/Group/
  Session 元数据、Pin、workspace 定向回归。不是全量测试。
- compileall 与暂存 diff check 退出码0。
- [final.xml](final.xml)、[final.log](final.log)、[exit-codes.json](exit-codes.json)、
  [tested-sha.txt](tested-sha.txt) 可直接复核。
- 5条现有 warnings：pytest-timeout 插件不在当前解释器导致 timeout 配置/旧标记
  提示，另有现有 TestClient/httpx 弃用提示；未新增 skip/xfail 或弱化断言。
- 初轮2 failed/35 passed，退出码1，见 initial.log/xml：测试 helper 把空谓词替换为
  默认谓词，以及 Session.create 不接受 managed_by 参数。修正测试构造后均通过。
- 第一次最终组合没有终态/JUnit，执行会话在续作消息后不可恢复，退出码未知；
  当时核验没有对应 pytest/HTTP/fakeCLI 残留。保留 interrupted-final.log，不将其
  算作通过；随后在相同实现 SHA 上完整重跑取得本轮最终192项结果。
- 文本日志和失败 JUnit 堆栈只统一换行、去除行尾空格，错误正文保留。

确定性用例覆盖字段/类型、schema与实际验证一致、深度/节点/值/候选/结果边界、
冷加载候选上限、无 history hydration、字段权威投影、冻结后真实改名/移动/新增、
逐目标部分失败、零结果、溢出时无副作用、恢复不重新解析、disabled草稿不可读取或
操作其他 Session、旧 Group/provider 门禁及匿名 enable 拒绝。

## 真实隔离 HTTP + fakeCLI

query 实例端口 **58025**、PID **20476**，临时根/命令/createTime/Event/Run/
selection/targetResults/queue/fakeRecords 见 [http-evidence.json](http-evidence.json)。
3个自有 Session 使用确定性 fake stream CLI，无收费 provider。

实测默认 disabled 不暂停；通过 disabled Action 草稿再由 source API启用，用户
登录 enable Trigger，注销后 source真实 running归零仍触发；七字段组合 query
从3个候选选中目标1个，持久 targetIds/selection 后原 Pause 业务落盘。解锁目标
队列消息后仍不交付，未选中 Session 正常交付。匿名 enable 明确401。
`portReleased=true`、`ownedProcessesRemaining=[]`、`sentinelUnchanged=true`。
explicit 对照本轮证据另存 explicit-http-evidence.json；原核心历史证据路径恢复为
原提交内容。本次只在本工作树和自有临时根运行，未触及用户 Session/真实 data。

## 边界

- 浅元数据投影是执行开始的一次观测，不承诺跨全部 Session/Pin 的事务性快照；
  固定 targetIds 落盘之后不重算，实际业务仍逐目标检查。
- Session.list_all 仅增加可选内部 limit，默认调用行为不变；候选上限会保守计入
  冷存储中的 .json 候选文件，错误/异常元数据不扩大选择为全体。
- 未增加可靠 Event Store、重放/重试、全局用户鉴权；原核心长期账本/大规模负载
  和真人验收边界仍适用。本轮不宣称真人 UI、收费 provider 或 OS 断电验收。
- 没有全量、共享依赖写入、子派发、React或外部插件适配；未移动 main/practical、
  push/release、访问8767/8768或操作现有服务。

提交 clean 候选后停止，等待 MA 独立验收。
