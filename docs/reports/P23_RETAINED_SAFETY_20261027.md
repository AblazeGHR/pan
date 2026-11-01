# P23 retained-safety 公共取消接线交付

日期：2026-10-27；工作树 `D:/project/pan-worktrees/job-p23-retained-safety-20261027`；
分支 `r6/p23-retained-safety-20261027`。固定继承基线仍为
`bdd4c778d55daff58529635b2adc464c03a1393c`，未追基线、未 merge/cherry-pick P21/P24。
公共接线源码提交：`4d9ab1c8857f8caa4a3150c55a03fdb8e7f527d3`；报告/证据后续普通提交，最终 clean SHA 见交付答复。
MA 最新告知 integration 为 `5519630951f33417eacd57a8e0c2643d3016ea7f`（P21/P24 已组合），
本候选没有合入该树，仍需 MA 方法级组合及统一验收。main/practical 未由本任务推进。

**本轮结论：三个指定原红节点均已有通过的精确 canonical 后继；两条 HTTP 后继严格走公共
Run cancel，`canonicalCleanupRequested=true`，无诊断 fallback 依赖。默认 pending/uncertain
的原 identity 查询恢复、真实资源清理门禁和 venv redirector 身份验证已补齐。** 原三个 legacy
测试、断言及原红证据保留，扩大矩阵仍显式显示这三红；未恢复 legacy start/dispatcher/reconcile。

## MA 固定 a700 快照反馈后的指定 venv 补验

MA 反馈固定 `a700f220` 的14项为8pass/6fail，失败停在旧 fixture 将 Windows venv
launcher PID 等同真实 supervisor PID 的断言。该结果属于旧快照；本树此前已普通提交
`4d9ab1c8`（源码/测试）和 `260f2418`（报告/证据），本轮开始实查为 clean，并非仍有五个
未提交产品文件。所有在途成果已保留，没有 reset 或追基线合并。

本次直接使用 MA 指定 `D:/project/Pan-main/.venv/Scripts/python.exe`，与 system Python 各跑
四个 retained-safety 文件26项（protocol8 + owned3 + canonical3 + public12），均通过隔离 wrapper。
精确 argv、实际 exit、逐 node JUnit、用时和资源证明见
[ma-venv-recheck/validation.json](../../evidence/p23-retained-safety/ma-venv-recheck/validation.json)。
这是指定既有 venv 入口补验，与前轮自行创建的隔离 venv 证据分开；没有安装或修改该 venv。

| 本次入口（源码4d9ab1c8） | protocol | owned | canonical | public | 合计 | fail/error/skip | exit |
|---|---:|---:|---:|---:|---:|---|---:|
| system Python 3.14.5 | 8 | 3 | 3 | 12 | 26 passed | 0/0/0 | 0 |
| D:/project/Pan-main/.venv/Scripts/python.exe 3.14.5 | 8 | 3 | 3 | 12 | 26 passed | 0/0/0 | 0 |

system 用时117.170秒，指定 venv 用时122.830秒。
两种入口各13份资源工件，均 portReleased=true、ownedProcessesRemaining=[]；公共HTTP False/True
均 canonicalCleanupRequested=true、effect=1、cleanupCalls=1。指定venv实际观察到
launcherIsSupervisor=false、ownershipRootIsScript=false，原生FILETIME/祖先关系断言全部通过。
本次原始JUnit/log字节保存在新目录raw-output.zip；validation.json列归档与审阅文本SHA256。

本轮不修改产品代码/测试，源码仍为 `4d9ab1c8`；此前所有原红和资源工件字节保持不变。

身份修复分别采集 launcher、可信 execution document 中真实 supervisor、owned root、脚本
parent/child 的原始 FILETIME，并逐项 native verify。发生 PID 差异时核验真实祖先关系；再比较
trusted document 中 birth 与 native identity。没有无条件允许 PID 不同，也没有用路径字符串猜测。
正常结束须 business owned tree 无剩余、真实 supervisor 与 launcher 都退出且随机端口释放；
异常 teardown 先用已核验 supervisor identity 清理，再等待保留的 Popen launcher，不能只收 launcher。

`execution_host.supervisor_exit_evidence` 的职责及严格 unknown/PID复用边界补充到
[本次 capability 矩阵](../../evidence/p23-retained-safety/ma-venv-recheck/CAPABILITY_MATRIX.md)。
沿用 processOwner，不新增平台能力。该查询不 kill；原 supervisor 仍活或状态未决时不确认完成，
PID absence 只作退出后置证明。真实延迟 supervisor 测试与缺 proof 负控保留。Windows 实测，
POSIX/三平台CI仍未验。MA 重新验收和集成组合尚未完成；本 TA 不自行宣称接受。

## 当前逐 node 映射

| 原 node（tests/ 下） | 精确 canonical 后继（tests/ 下） | 当前结果 |
|---|---|---|
| `test_job_delete_safety.py::test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent` | `test_job_retained_safety_canonical.py::test_terminal_child_writeback_does_not_recreate_retired_parent_successor` | native/venv 通过；真实 terminal 迟投递，retired parent 全字段不变、无替代 parent、新准入 409、效果/清理各一次 |
| `test_job_delete_http_owned.py::test_real_owned_tree_delete_http[False]` | `test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[False]` | native/venv 通过；真实公共 cancel 发起原 executor cleanup，完整 owned proof 和 supervisor 退出后原 operation completed，再逻辑 retire |
| `test_job_delete_http_owned.py::test_real_owned_tree_delete_http[True]` | `test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[True]` | native/venv 通过；首次 unknown 不 kill/不确认，GET/冷恢复不重试；明确新公共请求 revision 2 后才清理确认 |

executor 精确对应另见 `test_job_retained_safety_owned.py::test_real_executor_cleanup_http[False/True]`。
**definition DELETE 仍为逻辑 retire，不等于物理删除，也不隐式 kill。** unknown 拒绝的是资源已清理
确认，不能据此声称物理 purge 成功。本任务未添加物理删除产品接口。

## 授权公共接线与兼容

相对初轮 `a700f220`，本轮修改五个产品文件：`final_service.py`、`final_api.py`、
`final_runtime/ledger.py`、`independent_executor.py`、`core/platform/execution_host.py`。
相对固定 bdd 基线还包含初轮三个 independent runner/service/client 文件，共八个产品文件。
完整相对 diff 及逐 node 结果见 [public-resume/validation.json](../../evidence/p23-retained-safety/public-resume/validation.json)。

- `control_run` 在原事务中捕获 job/run/step/attempt/lease/epoch/generation/effectKey，持久化 cancel
  intent 与 revision；`control_run_postcommit` 提交 requested 后才锁外调用原 executor。重放同
  requestKey 或并发已在途请求不增加 cleanup。queued/pre-effect 快路保留；无资源证明保持 pending/unknown。
- `final_api` 保留授权、schema、CAS、requestKey，通过可 await 的 postcommit port 执行 Run control；
  同步 fake port 保留原接线。unknown 可按原 operationId/requestKey 查询；明确新请求才复查。
- dispatch/_drive_run 默认 process 使用 canonical root 下的独立执行 Host，不新增 legacy Job 生命周期。
  默认 execution_pending 导致的 uncertain 用原 Attempt/Run/effectKey 查回 terminal，不再准入或重发效果。
  普通 GET/recover/receipt lookup 均不发 cleanup；冷恢复将未完成 intent 留为 unknown。
- Ledger 只新增 `reconcile_attempt`：严格校验原完整 fence、最新 step generation、唯一同 key
  unknown/partial effect 和 trusted terminal。保留原不确定 effect 历史，更新同 Attempt/Step/effect。
  `finish_attempt` 方法 AST 与 a700 完全一致，仍拒绝 uncertain，未放松原门禁。
- cancel 完成必须具备 confirmed、identityVerified、treeTerminated、完整 ownedTree（remaining=[]）
  及原 supervisor 退出证明。实际树已清空而 supervisor 尚活的真实测试仍阻止 completed/cancelled。
  新增 supervisor 查询只读，PID absence 仅作为退出后置证明，不能授权 kill；unknown 从不当成功。
- 保留旧 immutable `product_process` 的 `pan.typed.v2` publication/cancel=false；新增
  `product_process_cleanup_v1`，provider releaseDigest=`digest("pan.typed.process-cleanup.v1")`、
  generation=2、cancel/reconcile=true。重复发布完全一致；不同内容覆盖旧 identity 仍拒绝。
  实跑旧 frozen plan 不变并用原回执恢复，不能把新能力硬覆盖到旧 release。

P21 独占的 `FinalService.actions()` AST 未改；coordinator（含 P24 pin resolver）、React、
background_jobs、conftest、共用 support 和三个 scheduler host 均未改。
本树没带 P21 fe0ad770/P24 acddad3a；MA 应保留已集成 actions/pin 方法，组合本候选取消段，不能整文件覆盖。

## 实跑与资源证据

最终命令由 [run_validation.py](../../evidence/p23-retained-safety/public-resume/run_validation.py) 固定
完整 argv/cwd，全部经过 `scripts/run_tests_isolated.py`。对应 `*-command.json` 记录实际 exit 和用时；
JUnit、逐 node（含全部 skip 原因）及资源清理汇总在 validation.json。

| 最终 label | tests | passed | failed/error | skipped | exit |
|---|---:|---:|---:|---:|---:|
| backend-delivery（contract/runtime/http/transports/clients/admission/executors/independent/safety/platform/path-flip） | 535 | 513 | 3/0 | 19 | 1 |
| venv-delivery（四个 P23 retained-safety 文件） | 26 | 26 | 0/0 | 0 | 0 |
| import-delivery（隔离 import packages.web.server） | — | — | 0 | — | 0 |

前一轮最终源码 `backend-final` 同为 535=513pass/3fail/19skip；`venv-final` 为26pass。
三条红均为表中原 legacy 节点，错误仍为 retired reconcile/start，没有新失败。
19 skip 保留为现有 POSIX ProcessOwner 18 条和 Windows-constructor-on-POSIX 1 条，未吞 skip。
新公共测试覆盖：default pending 冷重开；原 operation 丢响应查询和请求重放；unknown 显式复查；
CAS/匿名拒绝；supervisor 退出门禁；queued 零进程；旧 epoch/generation/lease/Attempt 拒绝且原聚合不变；
五个不完整 proof 负控；真实 product 默认 executor；旧 release/frozen plan 兼容。

全部真实测试只建自有临时根、随机 loopback 端口和进程树。native 下三类进程，venv 下实际五类：
launcher redirector、真实 supervisor、owned root redirector、脚本解释器、child。分别验证 PID/原始
FILETIME 与祖先关系，不使用 Popen.pid==supervisor 的错误假设。故障只注入 denied probe/实际 kill
后的回执丢失/延迟 supervisor 退出；spawn、owned handle、kill、终止证明和 terminal 都来自真实 backend。
PID 复用负控是活 PID + 错 FILETIME 的真实 native 拒绝，未强迫 OS 实际回收复用 PID 数字。

[native 证据](../../evidence/p23-retained-safety/public-resume/native/) 和
[venv 证据](../../evidence/p23-retained-safety/public-resume/venv/) 各含 13 份工件，均证明端口释放、
ownedProcessesRemaining=[]；product 默认路径另证 productProcessesRemaining=[] 与 supervisor 已退出。
正常/unknown 显式复查 HTTP 后继 effect=1、kill=1；已发生的终止效果不自动重复。
隔离 venv 仅使用 `--system-site-packages --without-pip` 创建，没有网络安装。

初轮 `first` 收集缩进错误 exit2，`second` 曾因异步 unknown 未观测而复查请求被合并并出现 teardown
错误，均保留日志/XML；修复为显式新请求前先只读查证，并按真实 supervisor 身份清理 teardown。
`third` 6pass、contracts-first 298pass、public-first/expanded/complete 3/6/12pass、venv-first 12pass，
均留存；不把探索失败算通过。raw-output.zip 保存 resume 日志/XML 原字节，artifact-whitespace.json
列审阅文本规范化前后 SHA256。初轮全部原红和 canonicalCleanupRequested=false 工件仍原字节保存，并以历史明确标注。

Windows 实测；[更新 capability 矩阵](../../evidence/p23-retained-safety/public-resume/CAPABILITY_MATRIX.md)
列 native/venv 与未验平台。Linux/macOS 实跑、三平台远端 CI、完整仓库 suite、MA 组合、浏览器、
真实 provider、真实数据迁移、部署与人工验收均未证明。没有 merge/push、既有服务操作、8767/8768、
真实 data/profile 或51/60集合读写；未另派 TA。原测试生成的旧 audit 工件先复制到独占 evidence 再恢复，
不会交付外范围差异。

---

## 初轮历史（仅描述 a700f220，以上公共接线交付已取代其中共享入口阻塞结论）

以下原报告用于保留探索和原红证据，不代表当前候选仍有两条 canonical 后继红。

# P23 retained-safety 后继验证与最小修复

日期：2026-10-27。工作树：`D:/project/pan-worktrees/job-p23-retained-safety-20261027`。
分支：`r6/p23-retained-safety-20261027`。
固定基线：`bdd4c778d55daff58529635b2adc464c03a1393c`；它只是尚未整体 MA 验收的
P21 候选接口基线，**不表示已合入**。任务给定集成树仍为 `bac726cf`，本 TA 未推进它。
源码/测试/证据候选提交：`38083c99d77cb800bb789ff8b952fa26d6a30b27`。
本报告及证据文本规范化另作普通提交；最终 clean 交付 SHA 以本 Session 最终答复为准。

**结论：executor 保留安全能力已补最小实现并经 Windows 真树验证；原 late-child 节点的
canonical 逻辑 retire 后继已通过；两条完整 HTTP 后继仍阻塞于共享 Run cancel 接线，不能宣布
3 条 retained-safety-gap 全关闭。** 原测试/断言和 R6-I 原红证据未删、未 skip、未弱化。

## 逐 node 映射

| 原 node | 精确后继（路径相对 tests/） | 本轮结果与边界 |
|---|---|---|
| `test_job_delete_safety.py::test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent` | `test_job_retained_safety_canonical.py::test_terminal_child_writeback_does_not_recreate_retired_parent_successor` | 后继通过。真实独立进程产生 terminal receipt 后暂缓投递；原 Job retire，再投递真实 terminal，原 ID/定义保持 retired 且字节字段不变、无替代 parent、新 Run 准入 409、业务效果和清理各一次。原节点因 retired `_reconcile_scheduled_processes` 仍红。canonical 保留历史 tombstone，不声称物理删除 parent 记录 |
| `test_job_delete_http_owned.py::test_real_owned_tree_delete_http[False]` | `test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[False]`；executor 对应 `test_job_retained_safety_owned.py::test_real_executor_cleanup_http[False]` | executor 通过；完整后继红在 canonical cancel 未向原 executor 请求 cleanup。测试显式诊断 fallback 调用真实独立 cleanup HTTP 完成清理，**不把 fallback 算公共入口验收** |
| `test_job_delete_http_owned.py::test_real_owned_tree_delete_http[True]` | `test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[True]`；executor 对应 `test_job_retained_safety_owned.py::test_real_executor_cleanup_http[True]` | executor 首次 unknown 拒绝确认、冷进程查证和明确复查通过；完整后继同样红在共享 cancel 未接线。公共 unknown 拒绝物理删除→查证→确认的完整协议仍需 MA 接管验证 |

可直接核查的机器结果见 [validation.json](../../evidence/p23-retained-safety/validation.json)。

## 改动与接口

只改 4 个授权产品文件：`packages/jobs/independent_executor.py`、
`independent_execution_runner.py`、`independent_service.py`、`independent_client.py`。
新增 3 个 `test_job_retained_safety_*.py`，未改两个原测试文件。

- 新增内部 `POST /executions/{originalEffectId}/cleanup`，body 只允许完整原 `attempt`。
  API 不接受 caller 自报 proof/PID/birth；沿用本地 Host/Origin/content-type 防护。原 effect 不存在
  404，旧 Attempt/lease/epoch/generation 或无原 owned handle 409，`processOwner` 不支持显式 501。
  `IndependentExecutor.cleanup_execution(*, attempt, effect_key)` 提供共享层可调用的 async port，
  并复核 Job/effect/Attempt 全 fence。原 pre-effect cancel 语义未变。
- execution document 新增 `cleanupRequest={revision,status,proof?}` 和 `cleanupHistory`，不新增
  SQLite 表、不改 canonical Job/Run/Step/Attempt/schema；status 为 requested/unknown/confirmed。
  HTTP 200 只是返回状态，不把 requested 或 unknown 当成功。请求和确认分开持久化。
- 整树终止只由持有原 ProcessOwner/Job handle 的 canonical supervisor 执行。先核验原 PID/birth，
  Windows 使用原始 FILETIME，再沿现有 owned-tree 算法终止、等待根进程，持久保存身份/根退出/
  owned 成员/remaining 证明。已证明的 tree 结果在后续 terminal 阶段复用，不再次 kill。
- 首次 probe unknown 时不执行 kill；真实终止已发生而回执丢失时保持 unknown 并保留原 handle，
  不因根进程后来退出或普通恢复而自动重复清理。只有下一次明确请求才增加 revision、复查。
  已结束但仍由保留句柄识别的同一根进程允许 native dead+ours=true 查证；unknown/reused 拒绝。
  supervisor 丢失时不回落到 root-only recovery 冒充树证明。杀掉业务进程的效果为 rejected，非 succeeded。

**canonical DELETE 保持逻辑 retire。** 没有将其修改成隐式物理 purge，也没有要求它仅因
运行中就必须返回物理删除的成功/失败语义。先显式清理原执行资源、再 retire 的后继已真实走到；
公共 cancel 发起这一清理的共享入口仍缺。早期 `canonical-first` 实验错误要求逻辑 retire 返回 409，
该探索断言已纠正，原日志/XML 仍留存，不算验收依据。

## 真树证据

测试使用自建随机 loopback 端口、临时根和自己的 supervisor/parent/child 进程。测试启动器运行
生产 `independent_execution_runner.run`，其 spawn/wait/verify/kill_tree 均走真实 Windows backend。
只注入两种不确定性：identity probe denied、真实 tree kill 后确认回执丢失。没有 mock 活树、终止、
成功 cleanup proof 或 terminal receipt。canonical 测试的 ExecutorPort 等待真实独立 terminal 并可延迟
投递；这证明真实迟到回调，不代表默认 execution_pending 的恢复接线已完成。

- [普通清理](../../evidence/p23-retained-safety/backend-unknown-false.json)：三类原进程身份可查证，
  native owned tree 清空，effect=1、kill=1，原 fence 重查和重复准入不重发。
- [unknown 后复查](../../evidence/p23-retained-safety/backend-unknown-true.json)：首次 denied 不确认也
  不 kill，冷解释器 recovery exit 0，去掉故障后 GET 仍 unknown，显式 revision 2 才确认；effect=1、kill=1。
- [实际已终止但确认丢失](../../evidence/p23-retained-safety/backend-partial-ack.json)：根/子已真退出，
  supervisor 保留句柄；没有自动第二次 kill，明确复查后才确认。两次显式清理授权、业务 effect 仍为 1。
- [迟到 terminal](../../evidence/p23-retained-safety/canonical-late-terminal.json)：原 parent retired 不变，
  原 Run completed，无替代对象，无重复效果；新准入明确拒绝。
- 两个 [HTTP False](../../evidence/p23-retained-safety/canonical-delete-unknown-false.json) /
  [HTTP True](../../evidence/p23-retained-safety/canonical-delete-unknown-true.json) 工件均明确记录
  `canonicalCleanupRequested=false`；`physicallyCleanBeforeRetire=true` 来自真实内部清理，不能掩盖入口缺失。

PID 复用负控使用**真实活 PID + 故意不相等的 FILETIME birth**，native verify.ours=false，
recover 返回 refused_identity，原进程仍活。没有强迫操作系统实际循环复用 PID；不扩大此证据的含义。
所有真树工件最终记录 ownedProcessesRemaining=[]、supervisor 已 reap、随机端口已释放。

## 执行与统计

全部 pytest 命令均通过 `python scripts/run_tests_isolated.py -- python -m pytest ...`。
每次完整 argv/cwd/exit/逐 node 结果见 validation；各 label 对应同目录 `.xml/.log`。

| label | tests | passed | failed | skipped | exit |
|---|---:|---:|---:|---:|---:|
| original-red | 3 | 0 | 3 | 0 | 1 |
| backend-first | 2 | 2 | 0 | 0 | 0 |
| canonical-first（探索，含已纠正的 retire 假设） | 3 | 1 | 2 | 0 | 1 |
| canonical-final | 3 | 1 | 2 | 0 | 1 |
| regression-first（不存在文件名，未执行测试） | 0 | 0 | 0 | 0 | 4 |
| regression-final | 142 | 141 | 1 | 0 | 1 |
| matrix-final | 188 | 165 | 4 | 19 | 1 |
| delta-final | 11 | 11 | 0 | 0 | 0 |
| cleanup-final | 11 | 11 | 0 | 0 | 0 |
| acceptance-final（最终源码扩大矩阵） | 189 | 167 | 3 | 19 | 1 |

最终扩大命令：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_retained_safety_protocol.py tests/test_job_retained_safety_owned.py tests/test_job_retained_safety_canonical.py tests/test_job_final_independent_execution.py tests/test_independent_job_service.py tests/test_job_delete_safety.py tests/test_job_cleanup_failures.py tests/test_job_delete_main_combination.py tests/test_platform_process_contract.py -q --tb=short --junitxml=evidence/p23-retained-safety/acceptance-final.xml
```

最终新增 P23 集合：**14 nodes = 12 passed / 2 failed / 0 skipped**。
`acceptance-final` 加未改原 HTTP 两红，按 node 去重：**191 = 167 passed / 5 failed / 19 skipped**。
五条红＝原 3 红＋共享 cancel 接线后继 2 红；不是 5 个新产品回归。
19 skip＝现有 POSIX ProcessOwner 契约 18 条及 Windows-constructor-on-POSIX 门禁 1 条；理由在 JUnit。

`matrix-final` 另一次旧 `test_cancel_with_wrong_runner_identity_does_not_touch_job` 出现
`WinError 10053`，保留原始失败。`delta-final` 同 node 及最终 acceptance 同 node 均通过；没有压制错误、
放松断言或把该次失败冒称已实测 baseline-equivalent。`regression-first` 误写不存在的
`tests/test_independent_job_platform.py`，exit 4，也完整保留。

Windows `import packages.web.server` 经隔离 wrapper exit 0；未做 React 修改/构建。
证据 raw bytes 在 [raw-output.zip](../../evidence/p23-retained-safety/raw-output.zip)；审阅文本仅规范化
行尾空白/换行，前后 SHA256 在 artifact-whitespace.json，原 raw SHA 在 validation。
提交走普通 hook；最终范围 `git diff --check` 通过。

## MA 接管与未验层

共享入口补丁要求已精确写入
[MA_SHARED_ENTRY_REQUIREMENTS.md](../../evidence/p23-retained-safety/MA_SHARED_ENTRY_REQUIREMENTS.md)：
`FinalService.control_run(cancel)` 在事务提交后调用原 effect 的 cleanup port；`_drive_run` 不能仅凭
cancelling 宣告已取消；默认 pending adapter 的 uncertain Attempt 必须原 identity 查询/受 fence 的
reconcile，不能新建/重发；pending/unknown 保持可查，明确复查才重试清理；完整 proof 与 supervisor
退出后才完成资源清理；最后再宣告 process Action cancel capability。API 授权/CAS/requestKey 不放松，
DELETE retire 与物理资源清理继续分开。公共 unknown 拒绝/查证/确认门禁仍由 MA 补齐。

本 TA 没有直接改 shared final_service/final_api/ledger/coordinator/background_jobs/conftest/support/React；
三个 scheduler host 全部未改未删，P24 pins/history/bridge 范围未动。15 个关键受保护文件的
Git blob 与固定基线一致，R6-I 证据目录亦无差异。原 HTTP 红测试本身写入的两个旧 audit 工件仅在
本轮复现中产生，已将本轮输出复制至独占 evidence 后恢复原字节；未交付外范围改动。

[独立 capability 矩阵](../../evidence/p23-retained-safety/CAPABILITY_MATRIX.md) 记录已有 processOwner
能力的本次扩展。Windows 实测；Linux/macOS 执行、三平台远端 CI、完整仓库 suite、默认 pending
恢复、完整公共 physical-delete 流程、浏览器、真实 provider、真实数据迁移、部署与人工验收均未证明。
未另派 TA，未变更模型。未 merge/push、未推进 main/practical、未启停既有服务、未操作 8767/8768，
未读取或写入 51/60 集合。仅清理本测试创建且有归属证明的临时进程/端口。
