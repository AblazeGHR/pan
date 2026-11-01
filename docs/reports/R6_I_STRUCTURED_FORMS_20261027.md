# R6-I：恢复 canonical Scheduler / Job / Trigger 结构化编辑

源码提交 **`9aece5bbbe3729acb35ac7d292a6f01318aae68d`**，返修基线
`bdd4c778d55daff58529635b2adc464c03a1393c`，集成祖先
`bac726cf84c0510dff8618afb82e227e660f590c`。工作树仍为
`D:/project/pan-worktrees/job-p21-r6-time-events-20261025`，分支仍为
`r5/p21-r6-time-events-20261025`。后继证据提交不修改产品或测试源码；最终 clean 候选 SHA
见交付回复，可用本源码提交与最终候选的差异核实。

普通用户可通过字段创建、编辑 Scheduler、Job 和 Trigger，无需填写内部 definition/config JSON。
JSON 保留在可选高级入口。没有视觉重设计，也没有修改 Attempt、owner、receipt 或执行器行为。
这份候选仅交付 UI 退化返修及 MA 明确批准的 Action 引用目录元数据，不表示整个 R6-I
退役门禁、P23/P24 或真实数据迁移已经验收。

相对 bdd4c778 共 **13 个源码/测试路径，2028 additions / 30 deletions**，无整文件删除。
精确路径、Git blob、worktree SHA256、完整命令和节点见
[validation.json](../../evidence/r6i-forms/validation.json)。

| 范围 | 精确实现与行为 | 后继断言 |
|---|---|---|
| E12；S04/U08 的时间能力 UI 表达 | `web/src/components/automation/CanonicalSchedulerFields.tsx`、`EventRegister.tsx`。直接编辑 native rules；ruleId 稳定，添加/删除/多规则；once RFC3339 秒/任意小数/offset；decimal interval、anchor、grid/afterPublication；weekly 秒/weekday/timezone/DST；cron/timezone/DST；misfire/grace/pause/driveLimit。未触碰后端 calculator | `CanonicalForms.test.tsx` 前四条精度/多规则/策略/扩展字段断言；`AutomationWorkspace.test.tsx` roundtrip/CAS/unknown；真实浏览器两种组合的四类来源创建和编辑 |
| E11/E13；S08 的普通 Job 管理入口 | `CanonicalDefinitionFields.tsx`、`CanonicalJobsView.tsx`。Action 下拉、服务端固定引用、步骤添加/移动/删除、typed literals、Job input/Event/前序 Step 映射；前向引用阻止保存；现存步骤 ID、参数和 completion 不因无关编辑或目录刷新改变 | `CanonicalForms.test.tsx` Action/typed 参数/移动/input/固定引用后继；浏览器 `[True]` 表单创建两步 Job、移动验证、编辑、显式 enable |
| E11/E12；D1/D2 Trigger/来源关系 | 同上及 `fields.tsx`。可见 Job 表单承载 Action 链；Trigger 选择目标 Job、共享来源或 registered provider type；OR Events、AND/OR/NOT 条件、typed 比较值、参数绑定、filter。共享来源 revision 与 reference digest/review 保持 | `AutomationWorkspace.test.tsx` 来源选择、草稿/rebase/权限/原 operation；浏览器创建两个共享订阅并表单编辑其条件，验证 targetJobId/creatorSelector 保留 |
| U12 的 UI 使用边界 | Job policies 的 pin 编辑只写 definition `retention.pinned`，同 configRevision CAS，不调用 runtime `/pin`，不引入 override；恢复/overlap/通知/runLimit 的现存值保留 | `saves retention.pinned through canonical Job definition CAS without a runtime pin override`；策略保真组件断言；浏览器 `[True]` 读回 retention.pinned=true |
| U15；MA 单方法授权 | `packages/jobs/final_service.py::FinalService.actions()` 在原 `action.read` 和 readonly transaction 内返回 `items,nextCursor,references`；references 为 `{item.id: object_ref('action',item)}`。`canonicalJobs.ts` 扩展返回类型；无客户端自算 digest、无 object_ref 算法修改、无 final_api 修改 | `test_canonical_form_metadata.py` 两条：server ref 准确、native Job 接受、伪 digest 409/无额外 Job/效果；无效 Bearer 401/无元数据泄漏 |
| S04/S06；P23/P24 路径边界 | `store.py/time_events.py/legacy_bridge.py` 三文件与基线 blob 相同。`final_api.py`、`final_runtime/*`、`core/*`、`scheduler/*`、`conftest.py` 无本轮差异；FinalService AST 只有 actions 方法变化 | 可重跑 `evidence/r6i-forms/build_manifest.py` 的 AST/blob/ancestor 断言；本轮不改 P23 取消/dispatch/receipt 或 P24 pin 后端段 |

新表单以 native 定义作为编辑缓冲。只修改用户编辑的字段，保留其他规则、未知扩展和现存策略值，
没有经过旧单规则转换。时间与 duration 保留十进制字符串，不经过 JavaScript Date 或浮点往返。
选择另一种规则类型是用户明确替换该规则，保留其 ruleId；其他规则保持原值。既有 Action 引用的
revision/digest 明确展示，目录更新不自动升级引用；不把新修订的参数 schema 套在旧固定引用上。

原 CAS、共享 references digest/review、draft/conflict/rebase、unknown 按原 operation 查询与权限检查
继续使用原路径。高级 JSON 无效时文本保留；高级编辑切换不会被隐藏的结构化 required 字段误拦。
复杂数组/对象 literal 可选 JSON 编辑仍保留；普通 scalar、时间配置和条件比较值均有结构化入口。
本轮没有新增一个请求原子创建 Job+Trigger 的 composition 向导；浏览器验证的是先创建可见 Job，
再由 Trigger 引用同一 Job 的完整普通路径。

**最终验证：94 个不同 pytest 节点通过、0 fail/error/skip；React 8 文件 90 条通过、0 skip。**
这些是本轮实际执行数，不与旧报告的 1275 节点或历史次数相加。

| 最终命令/证据 | 实际结果 |
|---|---|
| `backend-final-v2.xml/log` | 91 passed，4 个依赖 deprecation/incomplete-definition warnings，exit 0。包括 metadata、native HTTP/service、真实 CLI/TCP、Scheduler 管理及依赖/startup/reopen |
| `browser-complete-final.xml/log` | 3 passed，exit 0。生产 bundle、authenticated HTTP、Chromium 153.0.8010.12、自有临时根和随机端口 |
| `vitest-final-v2.log` | 8 files / 90 passed，exit 0；完整 node 名保存在 validation.json |
| `tsc-final.log` | `pnpm exec tsc -b --noEmit`，exit 0 |
| `build-final.log` | `pnpm build`，exit 0；88 个 compressed assets 验证；原有 >500 kB chunk 提示保留 |
| `dependencies-final.json` | 359 Python files；static legacy imports=0、literal dynamic legacy imports=0、unreviewed production dynamic imports=0、parse errors=0，exit 0 |
| `source-commit.log` | 正常 pre-commit hook：React frontend OK，exit 0 |

全部最终 pytest 经过隔离 wrapper。完整命令如下；命令的 cwd/argv/exit/node 也已保存在 manifest。

```powershell
# repository root
python scripts/run_tests_isolated.py -- python -m pytest tests/test_canonical_form_metadata.py tests/test_job_final_http_service.py tests/test_job_final_clients.py tests/test_scheduler_creator_management.py tests/test_scheduler_dependency_cutover.py -o addopts= -q --junitxml=evidence/r6i-forms/backend-final-v2.xml
python scripts/run_tests_isolated.py -- .validation/r6i-venv/Scripts/python.exe -m pytest tests/test_scheduler_structured_browser.py tests/test_scheduler_management_browser.py -o addopts= -q --junitxml=evidence/r6i-forms/browser-complete-final.xml
python scripts/check_scheduler_dependencies.py --output evidence/r6i-forms/dependencies-final.json
python evidence/r6i-forms/build_manifest.py

# packages/web
pnpm exec vitest run src/components/automation src/views/AutomationWorkspace.test.tsx src/views/CanonicalJobsView.test.tsx src/views/AutomationView.test.tsx src/services/canonicalJobs.test.ts src/stores/automationSession.test.tsx src/router.retirement.test.tsx --reporter=verbose
pnpm exec tsc -b --noEmit
pnpm build
```

浏览器 `[True]` 从普通 Action 下拉开始创建 Job，填写 session/text 字段，添加第二步、绑定前序
输出；错误移动后 Save 禁用，移回后保存；再次编辑文字和 retention pin，读回 stable step IDs。
随后通过表单创建和编辑 interval/once/weekly/cron，包含
`0.125000000000000001`、`2027-11-01T00:00:12.123456789+08:00` 和含秒 weekly。
多规则添加后 ruleId 唯一且后续修改不变。两个 Trigger 选择同一 Creator，编辑条件后关系不丢。
服务端并发修改造成真实 409，fraction 草稿保留，rebase 后必须再次 review。
真实 PUT 提交后浏览器收到注入的 503，仅查询原 requestKey，写请求次数恰 1，最终值读回正确。

浏览器通过 input 事件实际统计所有带 JSON 标签的输入，普通路径计数为 **0**；无 pageerror、
无旧 event-sources 请求、providerCalls=0。最终三个 fixture 端口为 **60872 / 59303 / 51071**，
均确认释放；390/1440 无横向溢出。截图/结果分别见
[390](../../evidence/r6i-forms/forms-390.png)、[1440](../../evidence/r6i-forms/forms-1440.png)、
[完整 Job 表单链路结果](../../evidence/r6i-forms/browser-result-full.json)、
[来源/Trigger 独立组合](../../evidence/r6i-forms/browser-result-sources.json)、
[既有高级入口回归](../../evidence/r6i-forms/advanced-browser-result.json)。
fixture 中无关 shell discovery 使用已存测试 handler，关键 Job/Creator/Trigger/operation 路由与存储为真实实现；
这里的 503 只模拟提交后丢回执，不是伪造成功写入。

原失败证据全部保留，没有 skip/xfail 或断言削弱。新增 API 修改前 `action-metadata-current` 为
1 fail/1 pass，修改后 2 pass，并纳入最终 91。`vitest-v9` 的 pin 测试使用空步骤 fixture 被
真实校验阻止保存，修为有效现存步骤后通过，没有放宽链校验。`browser-complete-v1` 的默认 Python
缺 Playwright 导致 3 个导入失败，改用原验证 venv；子进程沿 sys.executable。
`browser-complete-v2` 两项在立即检查端口释放时失败，测试补同既有 fixture 的有界收尾等待，
仍强断言端口实际释放；后继全部通过。`backend-final.log` 保留了一次错误文件名导致的 exit 4 /
0 collected，正确命令为上面的 `test_job_final_clients.py`。其他早期定位/fixture 红在本目录原日志
和 XML 中保留。原始日志按字节归档到 [raw-output.zip](../../evidence/r6i-forms/raw-output.zip)，
每个日志和 archive SHA256 在 validation.json 中，未改写原字节。XML/HTML/补丁的原字节另在
[raw-artifacts.zip](../../evidence/r6i-forms/raw-artifacts.zip)；提交审阅副本只清理行尾空白，
manifest 逐文件校验其与归档原件的对应，断言、节点与错误内容保留。

**继承的 43 baseline 红未在本 UI 返修中消除、重跑或改断言，原 evidence bytes 不变。**
原 41 项逐条分类与后继仍见
[original-red-assessment.md](../../evidence/r6i-repair/original-red-assessment.md)：29 retired-positive、
1 stale-injection、1 history-fixture、1 bridge gap、6 metadata gaps、3 safety gaps。
另两项为原 baseline automatic-recovery 授权撤回注入和 quota unavailable HTTP，完整节点/命令见
[baseline-additional-command.json](../../evidence/r6i-repair/baseline-additional-command.json)。
P23/P24 已获 MA 分配的安全/历史/metadata/bridge 后继由其独立树及 MA 组合验收；本树没有合入或
冒领他们的修复，也不把旧正向断言直接改成 410 以消红。

未验证层：P23/P24 组合、三平台/远端 CI、真实 provider 消费、真实 profile 数据迁移与部署、
旧 bridge cursor/receipt/owner 终态仍待各自独立验收。三旧 host 保留，未执行整文件删除门禁。
51 已迁集合与 60 unrecognized 未做任何自动迁移、执行或删除，不宣称全 profile activeLegacy=0。
本轮未推进 main/practical、未 push、未启停已有服务、未访问真实数据或操作 8767/8768。

MA 单方法授权在本 Session 明确给出后才实施；接口协调使用
[Pan skill](C:/Users/14709/.agents/skills/pan/SKILL.md)，请求事务
`r6i-action-reference-metadata-20261027-1`，queueItemId `q_2c8ad0f15d3d424ab964cf0677baa08d`。
工具 accepted 仅代表 CLI admission；实际修改授权来自随后 MA 的明确答复。
