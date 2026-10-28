# CP-X1 revision 6：有限 fixture 修复批次

同一 Session / worktree / model，integration-only。起始为 clean CI 追加候选
**`e04a1528097f159bb0c4aad0491f8b9d534dcd12`**，包含先前 `1b319ed2`。
按 brief 无 reset/force 地 merge 精确 MA integration
`284abf3ca195149bd3746027203f566de40ab8cb`，无冲突，merge 基线为
**`264af19d0c854039e16bda7b598dfeb281fea85b`**。
本批 fixture 源码提交：**`982bf6076a913ccf6d68b2f1adfe4b7911151291`**。
仅改四个获准测试/fixture 文件，没有产品代码修改。

## 修改与原失败 disposition

| 原节点 / 分组 | 实际修改 | 本轮结果 / 仍开放的部分 |
|---|---|---|
| `test_agent_inbox_http::test_real_http_mcp_fake_cli` | `agent_inbox_http_server.identity` 的 jobs 路径应用实际 PAN_BACKGROUND_JOBS_DIR/default 策略；scheduler 投影改为 `platform.data_paths.job_root()`，不迁移/写产品根 | 原 HTTP/MCP/fakeCLI 节点 **1 passed**，包括不变的 roots containment、进程/监听身份、持久队列和退出断言 |
| `test_interaction_card_http` 的两个原节点 | 仅继承上面的 identity 投影修复；未改 card fixture | 两项都越过原 containment 后，在 `prepare_trigger` 旧 ledger 写入被 fence 拒绝，HTTP500，保持 red；browser 节点未进入 Chromium |
| `test_automation_concurrency::test_canonical_job_owns_concurrency_and_queued_cancel[queue-1/parallel-2]` 及 canonical provider 用户 | `test_automation_canonical.SessionProvider.send_session` 新增可选 `source_session_id`，记录在独立 `source_session_ids`；原 calls/targets/sources/未知/拒绝逻辑不改 | canonical/concurrency/retry 四模块 **44 passed**，含队列取消/effect 计数、unknown、failed retry 与恢复约束；独立 probe 证实 provenance/effect key 和 queued→unknown→rejected→queued |
| `test_quota_host_http::test_owned_host_configuration_read_projection_and_restart_real_http` | line45 改为实际 `monkeypatch.setattr(real_http,...)`；后获 MA 精确追加授权，确认 CodexQuotaStore.__init__ 的 `self.path` 后将两处 `store.patreal_http` 改为 `store.path` | 初次 AttributeError 原样保存；只追加重跑该原节点一次，现 red 为 line76 `host read-only projection` 超时。没有更改字节前后不变断言或继续改 host 产品 |
| `test_quota_policy_http::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http` | line45 改为实际 `monkeypatch.setattr(real_http,...)` | 越过 NameError/启动/认证/配置与 query admission，现 red 为 line82 `denied['ok']` KeyError；原旧-route envelope/权限断言不改，不将 query 200 宣称为整个原节点通过 |

原 485 分类、完整原 inventory 和上轮 NameError/containment/provider 红证据都不改写。
`fixture-contract.json` 核对其 Git blob 未变；本报告是新增 disposition，不把历史分类批量
改为成功，也不把新开放节点覆盖掉。Windows checkout CRLF 导致首次原字节比较不同；
校验用 `git hash-object --path` 与原 Git blob 对比，未清洗原证据。

## 实际验证与命令

指定 venv 是 Windows Python **3.14.5**；使用隔离 wrapper。没有全 Windows 重跑、
新 skip/xfail/ignore、原预期权限/fence/effect 计数修改。四文件全部原 Assert AST 一致，
仅把新增授权的 `store.patreal_http` 属性拼写归一为 `store.path` 后比较；counts 为
identity 1、canonical 3、quota-host 36、quota-policy 36。

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_automation_canonical.py tests/test_automation_concurrency.py tests/test_automation_retry_http.py tests/test_automation_retry_parallel.py tests/test_quota_host_http.py::test_owned_host_configuration_read_projection_and_restart_real_http tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http -q -ra --junitxml=evidence/cp-x1-fixture-batch-20261028/core.xml
# 44 passed / 2 failed / 0 errors / 0 skipped; exit 1; 18.593s
pnpm --dir packages/web install --frozen-lockfile
# exit 0; 仅本任务树 node_modules
pnpm --dir packages/web build
# exit 0; fresh production bundle; 88 compressed assets
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_agent_inbox_http.py tests/test_interaction_card_http.py -q -ra --junitxml=evidence/cp-x1-fixture-batch-20261028/owned-http.xml --basetemp=evidence/cp-x1-fixture-batch-20261028/owned-http-tmp
# 1 passed / 2 failed / 0 errors / 0 skipped; exit 1; 10.841s
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_host_http.py::test_owned_host_configuration_read_projection_and_restart_real_http -q -ra --junitxml=evidence/cp-x1-fixture-batch-20261028/quota-host-after-path.xml
# 1 failed / 0 errors / 0 skipped; exit 1; 20.940s
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe evidence/cp-x1-fixture-batch-20261028/check_fixture_contract.py
# exit 0: original asserts, provenance statuses/effect keys, preserved evidence, dist manifest
```

证据根 `evidence/cp-x1-fixture-batch-20261028/`；`validation.json` 分阶段列结果，
JUnit 只脱敏 hostname。`dist-provenance.json` 保存当前 merge 源码构建的逐文件 SHA256；
安装/build 输出单独保留，dist/node_modules 没有提交。browser 所需前提已完成，但 card
两项在 browser 之前失败，因此不能声称浏览器通过。
只复制 bounded server exception tail、inbox cleanup 和 quota downstream统计，未提交
`owned-http-tmp` 中含临时 key/profile/数据库的目录。两个旧 quota artifact 路径被 fixture
覆盖后，先复制本轮统计再恢复其原基线内容；未将旧证据覆盖混入候选。

## 卡片固定选项：精确生产路径与缺口

实际失败：`tests/support/interaction_card_http_server.py::prepare_trigger` line63/64
调用 `service.engine.actions.put` 和 **`service.ledger.put`**；后者经
`runtime_owner.legacy_writer → OwnerFence.writer → _check` 拒绝，
`ContractError: Job runtime owner epoch fence rejected writer`。两份 bounded stack 保留。
此 fixture 的 `TriggerReferenceAdapter` 提供 synthetic get/authorize/submit/lookup，
但这只替换后续 directed dispatch，不替代生产 trusted-choice 解析。

生产创建路径：`packages/interactions/api.py` 创建入口调用 `trusted.create`；
`packages/interactions/trusted.py::create → resolve` 在 `service.ledger.lock` 与
`actions_lock` 内读取 **`service.ledger.read()['triggers']`** 和
**`service.engine.actions.read()['actions']`**。它检查启用、事件类型、revision、固定
explicit Session target、producer/owner/managed scope，然后把 Trigger/Action digest、
授权和 targetIds 持久化到 Card `trustedChoices`。

响应路径：`api.respond` 对选中 choice 先调用 `trusted.verify`，同样读取以上 **legacy
ledger/Action store**，重验 definition digest 和 scope；之后 `store.respond` 固化响应，
`store.dispatch(...interaction_trigger_adapter)` 才走 synthetic/production directed port。
当前 resolver/verify **没有读取 canonical Trigger/Job/Action 图的分支**。

因此，仅把 fixture 初始化换成 canonical Trigger 创建还不等价：现 resolver 无法看见它。
最小 proposal 是 MA 另授权一个共享的 canonical trusted-choice 读适配：读取实际 canonical
Trigger revision/state、它引用的 Job frozen Action graph、可证明的 explicit Session targets；
生成并复验等价的 immutable binding/digest/scope，再将事件交给有 expectedRevision 与原
event identity 的 canonical admission。对应 fixture 通过真实 canonical publication/Job/
Trigger 接口设置同等固定目标和权限，仍验证 stale definition、scope 撤销、unknown 重查、
no-answer 与 no replay。若 Job 动态/多步图不能静态证明 fixed targets，应明确不支持/报告
产品缺口，不能把 synthetic adapter 的成功回执当作 trusted binding。
这需要 resolver/verify 的产品契约决策，**不属于本批可只改 fixture 的拼写/签名修复**。

遵守 MA 明确边界：未获取/冒充 retired owner、未临时解除 fence、未删除 trusted-choice
binding、未跳过 resolver，也未让 prepare_trigger 直接返回 ok。两条 card 原测试保持 red，
交 MA 协调最小后继；本轮不重做它们的诊断或再次跑完整测试。

没有 push、远端 CI、移动 main/practical、服务运维、真实数据/账号/profile或8767/8768。
没有修改 quota/IPC/POSIX/Job 产品实现。修后目标平台与下一次远端 CI 仍未验证。
