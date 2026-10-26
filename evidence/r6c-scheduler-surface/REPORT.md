# R6-C scheduler HTTP/MCP 对外面退役候选

工作树：`D:/project/pan-worktrees/job-p15-r6-scheduler-surface-20261025`；分支：`r5/p15-r6-scheduler-surface-20261025`；基线：`1fc385ff34b2e3fdddaf0cd066648a1a7ee1cd03`。起始工作区干净。

本包按调用方证据做有界退役。没有宣称整个 scheduler router 或全部九工具已删除。源码改动仅涉及两个 Python 生产模块：`packages/scheduler/api.py`、`packages/mcp/server.py`；同步仓库内 MCP manifest/系统提示、Pan 手册、测试与快照。未修改 canonical Jobs/Triggers/Creators、time/event 存储与历史、pins/retention/handoff、owner/fencing 实现。

| 对外面 | 本包处置 | 候选行为与原因 |
|---|---|---|
| `POST /api/scheduler/tasks/{task_id}/owner-switch` | delete | 无生产 HTTP 调用方；删 decorator、`switch_owner`、专用 `_switch` 和 R5 route 分类。旧地址 404，无写入/广播；内部历史 bridge/owner 恢复代码保持原样 |
| `scheduler_create/update/delete/pause/resume/run_now` | delete | 无仓库生产调用方；六函数、decorator 和专用写 helper `_scheduler_task_call` 删除。六名仅保留在退役清单/历史基线/负向测试；core tools/call 为 Unknown tool，native 为精确的 tool_unavailable |
| `scheduler_list/get/runs` | read-only | 明确保留历史需求与既有 ID。只调用 GET，不 claim；身份、managed 访问拒绝、target 过滤、URL quoting 和后端错误透传测试保留 |
| HTTP GET `/tasks`、`/tasks/{task_id}`、`/tasks/{task_id}/runs` | read-only | 三个 MCP 历史读工具依赖；不将旧任务 ID 伪装成 canonical ID，不删除历史 |
| HTTP GET `/next`、`/status` | read-only | 仍有前端 transport/store 代码；保持 nonexecuting preview/status，待客户端与 Creator 诊断入口迁移后再撤 |
| HTTP GET `/event-sources` | read-only | 路由挂载的 AutomationView → automationWorkspace 仍读取；保留 time/event 旧 source/pending/link/cursor 历史 |
| HTTP POST `/tasks`、PATCH/DELETE `/tasks/{task_id}`、POST `/tasks/{task_id}/{pause,resume,run-now}` | keep R5 refusal，后续包 | 前端 `api.ts → scheduleStore.ts` 仍有六类请求代码。当前生产路由只作已授权 410 / 匿名 401 的显式拒绝，不恢复写能力 |
| HTTP POST `/event-sources`、PUT/DELETE `/event-sources/{sid}` | keep R5 refusal，后续包 | EventRegister 在仍挂载的 AutomationView 内调用，尚缺 Creator 编辑/引用影响/revision 的客户端替换；当前仍为 R5 拒绝 |

调用方证据与缺失替代：

- `packages/web/src/services/api.ts:2458` 起：拼接 `${BASE}/scheduler/...` 的 transport，包含 tasks CRUD、pause/resume/run-now、runs、next/status。
- `packages/web/src/stores/scheduleStore.ts:9` 起导入 transport；`:115/132/149/164/170/174/181` 对应六类写调用。反向搜索未找到当前路由或组件导入此 store，`router.tsx:68` 附近的 `/schedules` 已重定向到 canonical；因此这是残留 transport/store 调用链，**不是已实测活跃 UI 流量**。按照“含 frontend transports，仍有调用方不删”的要求保守保留 HTTP 拒绝面；后续先清 transport/store/types 并决定旧 ID 的审计入口，再删六条拒绝路由。
- `packages/web/src/router.tsx:15/36` 动态加载并挂载 AutomationView；`views/AutomationView.tsx:63` 挂 EventRegister；`components/automation/EventRegister.tsx:41/52` 发起 event-source POST/PUT/DELETE；`stores/automationWorkspace.ts:18` 读取 event-sources。后续迁 Creator API、revision/referencesVersion、共享来源影响确认和恢复操作后，才可撤这四条 HTTP 路由。
- `packages/automation/authorization.py:49` 起保留历史 GET 的 IPC allowlist。未扩写权限或增加新写别名。
- `packages/web/server.py:135/11135/11136` 仍导入、绑定并挂载 scheduler API。`packages/web/e2e/jobs-badge-retry.server.py:30/33/53` 还使用广播状态；未删除整个模块或广播 plumbing。
- CLI：未发现生产 scheduler 命令/工具写调用；`packages/core/cli.py` 的 final/lifecycle 命令未改，相关 CLI 测试 24 passed。scripts 中的 scheduler/tasks 路径用于离线迁移/审计，不是 HTTP owner-switch 或 MCP 六写工具调用方。
- agents/manifest/系统提示：扫描仓库所有版本化文本及当前新增未忽略文件；无独立顶层 agents 目录。旧手册曾推荐六个工具，已改为三个历史只读工具；`packages/mcp/manifest.json:40` 同步为只读及 canonical 引导。根 manifest 无 scheduler 写调用。历史设计文档、基线 fixture、退役名清单保留为审计证据，不作为可用工具推荐。未写用户级 skill 副本。

反向引用工件：

- `references-before.json`：初始 6,323 个版本化文本文件；`references-after.json`：6,326 个版本化/新增未忽略文本文件，25 处 Python 动态 import、195 处 JS/TS lexical 动态 import。全部文件包含 tests/docs/scripts/manifests/历史审计，不用宽泛 tests/docs 排除掩盖命中。
- 引用只记录路径、行号和匹配 token；不复制源记录/日志载荷。Python import AST 与 JS/TS lexical import 结果供人工解析。已核对动态模块列表、前端懒加载和上述 transport 调用链；未发现指向已删除函数或 owner-switch 的生产动态调用。
- 扫描器：`scripts/audit_scheduler_surface.py`。此扫描证明仓库内已检索范围，不声称已部署 Worker、外部插件、用户脚本或真实 profile 的调用普查。

动态快照（`surface-before.json`、`surface-after.json`、`tests/fixtures/scheduler_surface_catalog.json`）：

- scheduler HTTP：16 → 15；唯一删除项为 `POST /api/scheduler/tasks/{task_id}/owner-switch`；新增为空。剩余 6 GET + 9 显式 R5 拒绝入口，逐项分类见上表。
- tools/list：118 → 118；集合差集 `added=[]`、`removed=[]`。基线 R5 已撤下六个 MCP 写工具的注册，本包删除其源码，因此不虚报 tools/list 再减少六个。
- scheduler 工具集合恒为 `{scheduler_list,scheduler_get,scheduler_runs}`。core/native 两种真实 stdio 的完整 tools/list 与 fixture 一致，无重复；六旧名 tools/call 均拒绝。函数 AST/模块命名空间检查保证不能通过 Python 直接调用残留写函数。
- `RETIRED_MCP_TOOLS` 继续完整记账历史名称；六个已删除符号不会先注册再 remove。canonical 工具、route 快照及其他 legacy reads/cancel 未变。

验证结果（机器可读见 `verification.json`，完整命令见 `commands.ps1`）：

| 层次 | 结果 |
|---|---|
| 修改前 API/MCP/R5 基线 | 107 passed、16 failed、0 skipped；16 项为旧 API 正向写测试仍调用已退役入口；原始 baseline.log/xml 保留 |
| 最终 scheduler API/MCP/退役核心四文件 | 140 passed、0 failed；其中 API 27、读 MCP 11、新退役验收 22、通用 R5 80 |
| 扩展组合回归 | 475 passed、42 failed、0 skipped，共 517；退出码 1；final-no-lifespan.log/xml 保留（此前 final.log/xml 亦保留） |
| 原生产模块基线回放 | 32 passed、42 failed、0 skipped，共 74；退出码 1；失败 node ID 与最终的 42 项逐项相同，newFailureNodes=[] |
| React | 3 files、38 passed：AutomationWorkspace 12、CanonicalJobsView 18、automationSession 8；退出码 0 |
| 依赖与静态 | pnpm frozen-lockfile 安装完成；git diff --check 通过；无 React 源码变更；没有运行生产 build |
| import/路由装配 | 生产 web.server、scheduler 历史 API、MCP 导入成功；route/OpenAPI、tools/list、源码删除断言通过；完整产品 startup/lifespan 未运行 |

组合回归中的通过项还包括：canonical migration 24、授权 13、canonical transports 62、Creators 16、canonical HTTP service 33、原路径翻转快照 1、handoff 8、completed retention 42、CLI 24、scheduler store 37、cron 43。pins 在旧 jobs API 中可独立执行的读/失败路径仍被覆盖；依赖已退役 producer 创建 fixture 的相关测试在下述 37 项中，不能将它们报告为全绿。

42 个既有红项分布：`test_jobs_api.py` 37、`test_scheduler_time_events.py` 1、`test_scheduler_integration.py` 4。其触发包括已删除的 `start_message`、`_run_job_action` 等 R6-A 旧宿主、R5 写接口的旧 200/ok 预期，以及未绑定授权的旧 HTTP 写 fixture。未降低断言、吞错误、skip/xfail，未恢复旧执行能力来消红。本包更新了与被删除 MCP 符号直接相关的测试，并将失效的 scheduler API 正向写测试替换为 401/410、历史读和零副作用验收；旧时间/校验/执行约束继续由 store/cron/canonical 测试覆盖。

基线回放方式：`replay_baseline.py` 从 git 读取基线两个变更生产 Python 模块的 blob，在独立隔离 Python 进程内装载。脚本先断言除此两模块之外没有其他生产 Python 差异、所选三个测试文件与 conftest 未改；不替换工作树文件，不修改真实数据。因此这是指定三套未改测试针对原生产代码的实测，不是仅凭报错形状推断。`verification.json` 对比完整失败 node ID；两边一致。

边界：本机 Windows、临时数据根、无产品 lifespan 的 ASGI 请求、真实 stdio 与临时本地 IPC。未运行整个仓库 pytest、旧 scheduler engine/dispatch/legacy_bridge 全套、三平台 CI、真实 HTTP listener/Chromium、完整服务启动、外部 Agent 普查、真实数据迁移或人工验收。没有 push、部署、启停现有服务、访问 8767/8768，或生产发布包 build。候选可供 review；扩展套件并非全绿，剩余 HTTP 入口按本报告依赖留待后续包。

终端日志提交前只规范化 UTF-8 显示文本的行尾空白/末尾空行；发生规范化的原始字节保存在相邻 `*.log.raw.gz`，原始与文本 SHA-256 记录在 `log-normalization.json`。JUnit、计数和失败 node ID 未改。

启动边界补充：初始基线和首次组合回归沿用仓库原有 R5 `TestClient` 上下文，进入过隔离临时 profile 的应用 lifespan；未操作现有部署服务。最终 `test_job_final_retirement.py` 已改用临时 `CanonicalUserAuth` 与无上下文的 client，只测生产路由和拒绝，不启动产品 watchdog/retention/plugin/terminal runtime。最终完整重跑 `final-no-lifespan.log/xml` 仍为 475 passed、42 failed、0 skipped，与基线失败 node ID 完全一致。
