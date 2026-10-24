# 已有 Session 替身交接候选

工作树 `D:/project/pan-worktrees/handoff-existing-session-20261025`，分支 `feature/handoff-existing-session-20261025`，基线 `f5cc2684a6a2391c626722cab10a5abeafe96745`。启动前核实 Git 根/分支/HEAD/clean。此报告是 TA 候选交付，不代表 MA 验收或主分支已整合。

用户经 MA 明确的最终决策：已有 B 的 original 元数据被所选来源 original 覆盖；实际 system_prompt 逐字保持，当前和后续 restart/resume/newspawn 不能从新 original/handoff 元数据合成注入；交接简报作为一条普通输入投递，受现有队列门禁控制。

## 实现与字段语义

完整逐字段来源/默认/显式覆盖表在 [交接手册](../skills/pan/references/handoff.md)，与 MCP docstring/schema、主 skill §2.7 及 HTTP 文档一致。HTTP `targetSessionId` / MCP `target_session_id` 省略仍走旧新建流程，指定则只复用已有 B，不存在拒绝。URL/`session_id` 始终是来源 A。

已有目标使用独立持久 `frozen_system_prompt` 保存交接前有效值，覆盖 `original_prompt` 与 `handoff_prompt` 仅作为元数据；有效值可与 original 不同，None/空串/空白同样保留。简报使用原队列类型、原消息 ID、ledger 与 idempotency index，返回 `briefDelivery`，A 保存 `handoffReceipt`。相同请求只核对原回执，参数变化或改回新建拒绝；未知结果不重新入队。

B 保留 name/history/CLI ID/adapter/config/workdir/template/pan_access/plugin bindings/约束/队列。默认不复制设置；显式 copy=true 与 adapter 参数拒绝。显式 model/permissionMode 经 B adapter 能力校验，仅无活/native Worker 时允许；不触发 restart/kill。B 原 managed/report/QQ/WeChat 与 A 关系合并去重；A 归档、B 管理 A，A 后续终态报告按新关系到达 B。A running、自调用、原队列、Pause/Illegal 与 Worker generation 保留。

已有模式对 A/B readonly、祖先/父子、不同 manager、损坏 backlinks/环路、不同有效 Workspace、第三方 QQ/WeChat 订阅冲突预检拒绝，测试确认零修改。跨 Workspace 归属尚未定义，MA 明确本批先拒绝并交付同组能力。HTTP 按 spawn→plugin→queue 顺序持有 A/B 锁，核心用 store 锁串行提交；写入失败恢复字段及已写 Session，Illegal incident membership/队列索引状态也回滚。持久回滚自身失败会报告错误，不伪成功。

MCP 已有模式对 source/target 均检查受管理访问权限，不以 claim=True 提前改变关系；HTTP 沿用内部 API 的 MCP 权限分工。GUI 在原模态框加入接班者下拉选项与三层提示（original 元数据 / 有效 system prompt / 普通简报）；旧新建选项与覆写流程保留。

## 验证命令与证据

基线原测试：`python -m pytest tests/test_session_handoff.py tests/test_handoff_original_prompt.py tests/test_handoff_job_retarget.py -q --disable-warnings --tb=short`，54 项通过，exit 0。

最终定向测试：

```powershell
python -m pytest tests/test_handoff_existing.py tests/test_handoff_existing_http_live.py tests/test_session_handoff.py tests/test_handoff_original_prompt.py tests/test_handoff_job_retarget.py -q --disable-warnings --tb=short --basetemp=.artifacts/handoff-existing/delivery --junitxml=.artifacts/handoff-existing/delivery.xml
```

84 passed，0 failures/errors/skips，exit 0。JUnit：[delivery.xml](../../.artifacts/handoff-existing/delivery.xml)。覆盖旧新建兼容、已有原上下文保留、非空且 original≠effective 的样本、显式设置/metadata 覆盖、空有效提示词、关系合并/冲突、readonly、Workspace 拒绝、注入持久失败回滚（含 Illegal incident）、并发重复请求、未知 ledger 状态不重投、HTTP 模糊参数零修改拒绝、真实 MCP 权限与 schema。

GUI：在 `packages/web` 设置本树 `.validation/handoff-ui/node_modules` 为 `NODE_PATH` 后运行 `node node_modules/vitest/vitest.mjs run src/components/session/HandoffSessionModal.test.tsx --reporter=dot`，8 passed，exit 0，日志 [ui.log](../../.artifacts/handoff-existing/ui.log)。`node node_modules/typescript/bin/tsc -b --pretty false`，exit 0。`git diff --check`，exit 0。

本树依赖 junction 指向 `D:/project/Pan-main/packages/web/node_modules`，没有更新其产品源码/依赖定义。共享 jsdom 缺 `@asamuzakjp/css-color` 的首次前端验证失败，在本树 `.validation/handoff-ui` 安装该包并通过 NODE_PATH 补齐后通过；没有修改共享包来掩盖失败。早期回滚测试只差 save 导致的 updated_at/summary revision 观察值，测试明确剔除这些观测值、仍逐字段确认业务状态恢复。早期 Illegal 测试缺持久 incident，补成符合生产账本的 fixture；失败 JUnit 保留在 [final.xml](../../.artifacts/handoff-existing/final.xml)，未弱化 Illegal 门禁。

真实 TCP HTTP/fakeCLI 测试使用 OS 随机安全端口、临时 profile，启动前确认 PID/checkout/runtime/port 身份，完全隔离 config/Session/Workspace/Jobs/日志。生产 HTTP 路由和 Worker queue/consumer 接到本地 fake CLI，B 在真实运行回合中交接；Pause 期间无简报写入，解除后只写一次，CLI/Worker 身份不变。重复请求始终原 queue ID，后续实际 resume/newspawn argv 不含 A 元数据或新简报、只含原有效 system prompt。证据在 `delivery/test_live_existing_running_cli0/owned-handoff-http/` 的 `verified.json`、`identity.json`、`cli-audit.jsonl` 和 `server.log`；测试 finally 关闭自有 Worker/服务并断言端口释放。

## 文档同步与边界

仓库 `docs/skills/pan/` 为主源。用户级 `C:/Users/14709/.agents/skills/pan/` 仅同步 SKILL §2.7 与工具表行、HTTP handoff 行、新 `references/handoff.md`、TA 操作入口新增链接；精确 diff 保存在 `.artifacts/handoff-existing/user-skill-*.diff`。已核对用户级 SKILL/TA 中 MA 新增 interaction_card 未获回答问题规则仍在，没有整文件覆盖或改 constraint/账号策略。

不提供跨多个 Session 文件的断电/进程崩溃原子事务保证：异常时回滚已验证，但回滚写入自身失败会明确报详情、需人工对账。Jobs retarget 沿用提交后独立逐项处理，失败不回滚已完成的 Session 交接。未验证真实 provider 消费、真实 QQ/WeChat 外部消息以及 GUI 真实浏览器交互；HTTP/fakeCLI 与 jsdom/TypeScript 证据分别陈述。未跑全量 CI/build，没有 push/release，没有移动 main/practical，也没有操作 8767/8768 或用户真实服务/profile/Worker。
