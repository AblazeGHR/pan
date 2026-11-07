# ACTION-UNIFICATION-20261104-P3 candidate

MA follow-up: “all Groups” acceptance remains blocked for legacy JSON Groups.
See [writer/schema handoff review](ACTION_UNIFICATION_P3_MA_REVIEW_20261106.md)
for the exact missing adoption port, candidate whitelist, P6 gates, and actual
direct MCP schema verification. The original 17/164 counts below describe the
first candidate, not closure of that acceptance item.

基线：`e27fa8a850da4bc9d27a321deb36f8034730103a`。隔离分支：
`implement/action-unification-p3-20261106`。开工时 worktree clean。
本报告与源码一起提交；最终候选 SHA 以交付消息和该提交为准。

实现依据为本轮用户指令及指定详细方案的 §4.2、§6、§8、§9.1、§12、§14、
§16/§19。指定的 agentflow JSON 仍是 planned 记录，未包含独立 revision 3
任务正文；已请求其路径。本候选不把该计划记录当成 revision 3 验收证明。

## 实现及存储边界

- `packages/actions/groups.py` 复用 FinalService 的目录解析、owner-fenced
  transaction、`final_documents` 与 `final_operations`；新增的 document kind
  是 `action_group` / `action_group_release`，未增加数据库、执行器或重试器。
- Group release 固定 ActionRef、工具别名、输入/输出 schema 和摘要。发布不
  修改旧 release，草稿 apply 不发布。不可变 capability 文件在事务中生成，
  只有提交后的 release pointer 才使其可发现；失败遗留的无引用目录不可执行。
- 复用 `action-group-<id>`、generic capability document、三种 MCP projection、
  binding admission、Worker pin、idle/deferred restart。canonical MCP 调用走
  P2 `FinalService.invoke_action`，原 JSON provider 路径继续服务原 release。
- 两类升级：指定 Action 在所有 canonical Groups 换到指定版本；单 Group
  全部工具换到服务端最新可用发布版。preview 展示固定 refs、对象 revision、
  schema/能力/效果/provider/完成/取消/reconcile/retry 差异及 blocker。
- apply 绑定完整目标列表、previewDigest、principal/profile/authorization
  revision 和原 requestKey。CAS、退役、不兼容、撤权或存储错误使整个 canonical
  批次回滚。上限 100 Groups × 每 Group 100 tools；大范围必须显式 groupIds 分批。
- 权限沿用 Action read/create/updateConfig/lifecycle/delete/invoke 和
  manageSystemActions。配置事务内重新检查当前权限，并持有原 UserAuth lock，
  Group membership 不产生调用 grant。实际效果边界继续由 P2 检查权限与 owner。
- MCP 工具 envelope 是 `{arguments,requestKey}`，ActionRef 从可信绑定 release
  解析。重复键返回原 operation，同键不同参数冲突；uncertain 只查原身份。

仍在 JSON 的 Group 不自动迁移，不建立多文件事务。preview 显式列出
`legacy_json_preview_only`；包含它们的 apply 被阻断。canonical 与 JSON 写入口
拒绝同 Group ID 的相互覆盖。JSON API/provider、旧 release、复杂/unknown 源记录
保持原路径；本候选不宣称 P6 迁移/退役或整体兼容闭环完成。

## 验证

均从当前隔离树运行 `python scripts/run_tests_isolated.py -- ...`，测试目录由
runner/conftest 隔离，未使用真实 profile、真实 data 或 8767/8768。

```text
python -m pytest tests/test_action_unification_p3.py -q --tb=short --junitxml=evidence/action-unification-p3/p3.xml
python -m pytest tests/test_action_unification_p1.py tests/test_action_unification_p2.py tests/test_action_unification_p3.py tests/test_action_groups.py tests/test_authorization_mcp.py tests/test_plugin_session_config.py tests/test_plugin_platform.py tests/test_action_persistence.py tests/test_action_ids.py -q --tb=short --junitxml=evidence/action-unification-p3/focused.xml
python -c "import packages.web.server; print('shared server import passed')"
git diff --check
```

P3 独立：17 passed，0 failed/error/skipped；组合回归：164 passed，
0 failed/error/skipped。组合计数见
[focused.xml](../../evidence/action-unification-p3/focused.xml)；P3 具名节点见
[p3.xml](../../evidence/action-unification-p3/p3.xml)。两项均 exit 0。

P3 测试覆盖：真实 MCP initialize/discover/call 的 gateway、standalone、native
三个子进程链路；真实本地 command/shell 执行；原 operationId 与同键冲突；
旧 pin/new pin 版本切换；invoke 撤权；HTTP ASGI 身份/CAS/build/unbuild/history；
并发同键 apply；并发编辑导致 stale preview；发布新 latest 后 preview 失效；
指定子批次；schema 不兼容；版本退役；第二次写入故障整批回滚；效果后回执前
注入异常，原身份 uncertain 查询且执行计数不增加；JSON 原字节保全及 ID 防覆盖；
IPC 先绑定、canonical service 后初始化/清除的生产启动顺序。

初始红记录：新测试夹具曾试图用 product publication 覆盖已发布内容，P1 正确
拒绝（2 failed）；已改用正常 edit→publish。真实 stdio 夹具曾缺少 cwd，P2
正确拒绝；已提供隔离目录。schema 差异夹具曾假定产品 schema 必有 properties；
已修正夹具。这些不是通过放宽断言或修改 P1/P2 保护得到的绿结果。

## 共享路径及 P4 整合

`packages/jobs/final_service.py` 无修改；继续调用 P1/P2 已有方法。
可能与 P4 交叠的共享路径：

| 文件 | 本片修改 | 整合要求 |
| --- | --- | --- |
| `packages/jobs/final_api.py` | install 中挂载 Group router（2 行） | 保留 P4 Job/Trigger 路由及其校验，不覆盖完整文件 |
| `packages/automation/authorization.py` | Group canonical path、惰性 service getter | 保留 P4 IPC allowlist/授权变化；getter 必须晚于真实 service 初始化解析 |
| `packages/plugins/runtime.py` | canonical Group catalog 投影、可信 release 标记、调用转接 | 保留原绑定/模式/pin gate；P4 扩展同一个 invoke_action 时核对 Group 调用兼容 |
| `packages/actions/store.py` | canonical catalog 投影与旧写入口同 ID 防覆盖 | P6 writer adoption 时替换受控迁移边界，不恢复双写 |

P4 若增加创建 Job 的 provenance 上下文，应从可信 Group release 传播，不能接受
业务 arguments 自报 release 或身份。当前 P3 只传已有 invoke_action 接口支持的
固定 ActionRef、arguments、requestKey；未修改 P4 的树或 Job/Trigger 合同。

## 未验证与交付状态

这是本地候选，未做 MA 整合/验收。没有全量 tests/ 运行、三平台 CI、真实 Group
HTTP socket/生产 Chromium、实际模型 Worker 的运行中换版/延后重启、真实数据
writer adoption、迁移、部署或人工验收证据。Worker pin 验证使用真实 runtime
和真实 stdio，但 Worker 启停边界由测试显式 capture 模拟。

前端源码未改，因此未执行 React typecheck/build；正常提交 hook 仍启用，未绕过。
没有 push、main/practical 推进、真实服务重启或真实数据写入。自动完成报告由本轮
最终交付消息承载，不向其它 Session 另发消息、不产生真实后台 Job。
