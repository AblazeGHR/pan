# CP-X1：真实远端失败修复与全量分诊候选

原 Session / 工作树 / 分支继续：`ses_cf3f911d4ef14201`、
`D:/project/pan-worktrees/xplat-job-fast-gates-20261027`、`xplat/job-fast-gates-20261027`。
起始核对为 **clean `036fe85f23b689195207d6794db0a70d78d87fc2`**，没有 reset、合分支、
派新 TA 或改变模型。修复代码 SHA：**`233c18b948d6013a0ba7934c71b1e2c79ff2e85c`**。

## 真实红证据与修复

[GitHub run 40637381532](https://github.com/AblazeGHR/pan/actions/runs/40637381532)
是实际 Windows/Linux/macOS CI，`headSha=5adbf3662b8e06e8eae13bf4aa5320a336bfa7a7`，
completed/failure。MA 下载的六份新 artifact 和 failure-inventory 是本轮唯一远端结果来源，
不是旧报告或本机模拟。原件未修改；副本 `remote-failure-inventory.json` 保留完整 traceback，
`windows-node-classification.json` 包含原 inventory 及两份 Windows XML 的 SHA-256。

Linux/macOS 全仓 collection 都在 `test_terminal_secret_store.py:679` 因无
`ctypes.WinDLL` 而 error。原 Windows-only skip decorator 无法阻止模块导入时的 DLL 初始化。

最小修复只将该文件的 `ctypes.wintypes` 导入、两个 WinDLL 及相应签名声明放入
`if WINDOWS:`。原用例已逐项有 `@windows_only`，沿用理由 `DPAPI / Windows ACL only`。
没有增加整模块 pytestmark，没有移除节点、降低断言或更改产品秘密存储实现。
与 clean 基线比较，**44 个顶层函数（含用例/fixture/helper）的完整 AST 相同**，包含全部
原断言和 decorator。新增 CP-X1 静态回归检查三项声明都位于 Windows 导入门内，且禁止
用整模块 skip 替代修复。它是导入边界回归，不是假造 sys.platform 的 POSIX 实机证明。

Windows 指定 venv 实测：secret-store 原 **40 项**与 helper **5 项**合计
**45 passed / 0 failure / 0 error / 0 skip**，exit 0，2.058 秒。
全仓 collection **7,835 项**，exit 0。没有重跑整个 fast、全量或已完成的旧验证。
修后真实 Linux/macOS collection 仍需 MA 的下一轮远端运行证明。

## 全 Windows 485 条记录的完整分类

`windows-node-classification.json` 对两份原 XML 逐 failure/error **记录**核对，不将 teardown
error 与同名 failure 合并。每条包含原 node/kind/message/完整 traceback、分类、根因组、
证据强度、最小 owner 建议、精确匹配的历史 successor mapping（无则 null）。
可重放命令：

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe evidence/cp-x1-remote-repair-20261028/classify_remote.py --remote D:/project/Pan/.workflow/job-unification-review-20261028/remote-ci-40637381532
```

| Windows artifact | testcase 记录 | failure/error 记录 | 退役协议 | 未解决契约/产品候选 | 环境/fixture | skip |
|---|---:|---:|---:|---:|---:|---:|
| Python 3.12 | 7,761 | 243 | 198 | 38 | 7 | 98 |
| Python 3.14 | 7,761 | 242 | 198 | 38 | 6 | 99 |

**198/version 的 retired-legacy-protocol 绝不表示迁移通过或原能力已有完整后继**。
其中有显式 RetiredEntryPoint、移除的 monkeypatch seam、旧 HTTP envelope、旧 runner 记录
和无 canonical owner 的 host；只是当前直接失败根因的分组，不是恢复旧执行入口的建议。
**38/version 的 new-regression-candidate 表示未解决的当前失败，不证明由本补丁新引入，
也不证明 baseline-equivalent**。环境/fixture 同样保留红项，不作为豁免。
3.12 唯一额外 node 是
`test_illegal_queue_wake_http::test_sticky_messages_wait_for_explicit_action_and_wake_precedes_backlog[start]`
的 setup error；不能从不同 skip 总数推断其修复或一致性。

每个版本只有 **38 条记录**找到精确原 node 的既有 mapping 引用，来源为
`evidence/job-retirement-closure/successors.json` 或 quota 的 `PROCESS_HTTP.md`。
保留原 mapping 的 status/boundary；历史的 strict-successor 标签不升级为本轮通过证明，
原 mapping 若自引用失败节点也只保留为历史记录。其余均明确 null，未按模块臆造覆盖。

## 已复现的代表、最小符号建议与所有权

第一次代表组在修改任何代码前的 clean `036fe85f` 执行；9 项全部失败，与远端可见症状
相符，仅证明这些指定节点在该本机基线上复现，不扩大为全部 485 项 baseline-equivalence。
第二组 3 项在 guard 修复后运行，涉及源码未改，全部保留失败。机器均为用户指定
`D:/project/Pan-main/.venv/Scripts/python.exe`，Windows Python **3.14.5**。

| 根因组 / 具体符号 | 本轮证据及最小后续动作（均未越权实施） |
|---|---|
| `background_jobs.__getattr__` / `retired.missing` | retry-if、jobs-api 两代表显式退役失败；把原行为逐项接入 canonical Run/Attempt 验证，不能恢复旧 writer 或批量改断言 |
| `test_settings_ui._UI_DEFAULTS` vs config/UI defaults | 1 代表失败，远端 4/version；新增 notifications/newError/lockerUnread 等字段产生精确结构漂移。先核准默认值契约，再补对应原能力测试，不改产品以适配旧固定 dict |
| `FinalService._install_product_actions`、`final_contracts` schema、`test_canonical_form_metadata` 的 `job_def` | 原 Action ref 校验正确后 native Job POST 400 invalid_schema 本机复现。定位完整 definition/Action input/Job argument schema，不能凭 400 就声称 source 字段是根因；目前仍 unresolved |
| `typed_executor._invoke_provider`、`test_automation_concurrency.held`、`test_automation_canonical.SessionProvider.send_session` | queue/parallel 两代表 calls=0。wrapper 接受 `**kwargs` 导致签名过滤保留 `source_session_id`，再转给不接受该 kw 的旧 test double，调用在记录前失败；`test_job_retirement_closure_successors.record` 也有同类 forwarding seam。由 owner 核对其作用与 canonical Run 状态，保留真实 concurrency/cancel/identity 断言，不把失效 provider 当成功 |
| `worker._persist_terminal_state`、`test_session_incremental` / `test_persistence_sharding` | 两代表复现：新增 durable terminal row 使 history 精确列表/末项 content 旧假设失效。核对 assistant 与 terminal 各自持久顺序，不删除 terminal 或削弱 durability 验证 |
| `test_system_fields` 导入 `tests.test_event_store.setup` | 原 helper 已不存在，ImportError 代表复现；由 fixture owner 迁到实际 canonical setup，保留 runtime issue ingress/observer 断言 |
| `test_quota_host_http` / `test_quota_policy_http` 的 `monkeypatcreal_http` | 两个 NameError 代表复现；最小修复是 fixture 变量拼写/绑定，由 owner 修，不改变 quota 业务断言 |
| `tests/support/agent_inbox_http_server.py::identity`、`scheduler.store.DEFAULT_ROOT` vs `platform.data_paths.job_root` | 资源根 containment 代表复现，remote inbox+card 共 3/version。共享 launcher 已转 data_paths，但 identity 仍报告 store.DEFAULT_ROOT；必须核对实际读写根并同步 fixture 的隔离/投影接口，**原 containment 断言不改**。不能仅换报告值掩盖真实未隔离存储 |
| `server._canonical_lifecycle_handoff`、`main_lifecycle` / `host_server.PanServer` | full 中 15/version lifecycle contract 和 7/version owned-host exit timeout；旧 registry 观察点与 canonical handoff 时序混杂。未本地触发这类生命周期操作，不将 timeout 统称旧协议；需要精确 stop-before-start / duplicate / owned lifetime successor 证明 |
| `test_scheduler_structured_browser` / `test_scheduler_management_browser` | native Job fixture 的400、admission envelope及浏览器 locator需分别核对；未本机重建/重复跑浏览器，不归因于本轮 collection patch |

分类 JSON 为其他节点保留逐条最小建议。授权仅 collection/CI，所以上述共享 fixture/
产品符号均只诊断回报 MA，未修改；Windows 485 条红记录本候选修复数量为 **0**。

## 精确本地命令与证据

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_background_job_retry_if.py::test_accepted_cancel_beats_orphan_recovery tests/test_jobs_api.py::test_action_template_default_assign tests/test_settings_ui.py::test_load_config_falls_back_to_ui_defaults_when_key_absent tests/test_canonical_form_metadata.py::test_action_catalog_reference_is_actual_definition_digest_and_accepted_for_native_job tests/test_automation_concurrency.py::test_canonical_job_owns_concurrency_and_queued_cancel tests/test_session_incremental.py::test_worker_path_saves_incrementally_and_reloads tests/test_persistence_sharding.py::test_usage_merge_and_terminal_events_keep_durable_order tests/test_system_fields.py::test_service_persists_runtime_issue_and_keeps_quota_observer -q -ra --junitxml=evidence/cp-x1-remote-repair-20261028/baseline-representatives.xml
# exit 1: 9 failed / 0 errors / 0 skipped
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_host_http.py::test_owned_host_configuration_read_projection_and_restart_real_http tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http tests/test_agent_inbox_http.py::test_real_http_mcp_fake_cli -q -ra --junitxml=evidence/cp-x1-remote-repair-20261028/fixture-representatives.xml
# exit 1: 3 failed / 0 errors / 0 skipped
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_terminal_secret_store.py tests/test_platform_fast_gate.py -q -ra --junitxml=evidence/cp-x1-remote-repair-20261028/windows-secret-store.xml
# exit 0: 45 passed / 0 errors / 0 skipped
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe scripts/platform_fast_gate.py --phase collect --output evidence/cp-x1-remote-repair-20261028/collection
# exit 0: 7835 collected
```

四条命令日志、XML、collection argv/results、函数 AST 保留结论与计数均在
`evidence/cp-x1-remote-repair-20261028/`。JUnit 只脱敏 hostname；原失败 assertion/traceback
保留。未安装 canonical 依赖，未使用 sys.platform 模拟代替实机，未对原失败新增 skip。
源码/报告的 diff whitespace 检查通过；全证据 diff 检查会报告 pytest 原始红日志/XML
traceback 的行尾空格，原样保留，不清洗失败证据或声称全证据检查通过。

## 与其他原 TA 的边界及尚未验证层

CP-X2 持有 POSIX identity/process/log；已确认其本轮有界脱敏 proof 写入
`evidence/cp-x1-ci/cp-x2-proof/`，当前 helper 不清空此输出，既有 artifact upload 整目录
覆盖，无需增加上传历史 `artifacts/cp-x2`。节点入口不变，不重复它的修复。

CP-X3 持有 plugin IPC startup。remote Ubuntu history-browser 的实际失败是启动 traceback，
不是 UI assertion；quota restart 在 POSIX 也出现 `AF_UNIX path too long`。
MA 另确认 CP-X3 本地旧 dist history 红属于旧 bundle 前提，**不作为 036 UI 回归证据**；
后续 MA 明确提供 CP-X3 `99e51f1f`（产品 `cf7510f5`）fresh-build 后未改动的 history
browser **1 passed**，并表示已源码审查、独立 5 项 endpoint 验证及 FF 整合。这是 MA 提供的
CP-X3 本机验证事实，不是本候选本机复跑或修后 Ubuntu CI 通过证明。

按同次 MA 授权，追加 `tests/test_plugin_ipc_endpoint.py` 到 `FAST_TESTS`，保留全部
24 个原条目。已从 CP-X3 工作树审查该文件的 5 个用例，涵盖真实宿主 concurrent bridge
roundtrip/cleanup、长 caller path、长 temp root、listener/start 失败清理、未知文件拒绝递归
删除。Windows 的短 Unix endpoint 分支自述为 policy probe，不当作 Unix transport 实测。
没有合入或复制 CP-X3 测试/产品；该文件将在 MA combined tree 中存在，本任务树当前缺失。
不使用可选发现/ignore 隐藏缺失，组合后 full fast 必须真实执行该文件。
本轮仅补跑 helper 6 项并核对相对 `1b319ed2` 的清单差异为唯一追加，未重复原 45 项、
全仓 collection 或 browser。追加接线证据见 `ipc-gate-addition.json`、`ipc-helper.xml`。

已有实际三平台远端红证据，原“没有目标环境”状态已被替换；但本次修复后未 push/
dispatch、未运行新远端 CI，也未验证修后 Linux/macOS collection、CP-X2/X3 组合或完整
Windows full。既有门禁/自动触发器/所有失败均保留，没有“CI 已绿”结论。
最终由 MA 合并必要的 narrow fixes 后授权下一轮 coherent remote run。
未操作 main/practical、既有服务、真实 profile/data/accounts/rotation Job、部署或8767/8768。
