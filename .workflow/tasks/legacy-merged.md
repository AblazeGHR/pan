# 早期已合入任务详情

> 当前任务卡、阶段/状态和下一动作只在 `../overview.md` 维护。本文件仅保留实现摘要、交付事实和验证证据，避免形成第二套待办。

## T-005：practical 启动脚本与 MCP 依赖检查

- 任务概括：补齐 practical 启动脚本对 Pan Core 和 stdio MCP 依赖的检查，让启动失败能被提前、明确地发现。

- 结论：启动脚本补齐 Pan Core 与 stdio MCP 依赖检查，保留 practical 用户脚本修改。
- 交付：提交 `9138a79`，已合入 main；未 push。
- 验证边界：启动脚本和依赖检查已验证；开发者验收仍由 overview 统一跟踪。

## T-006：Codex 全局额度缓存

- 任务概括：缓存 Codex provider profile 的额度并投影到 Session Details，使额度在离线或重复打开时仍可快速查看。

- 结论：额度归属于 Codex provider profile/账号，不属于 Session；实现 profile 缓存、app-server push 持久化、离线 API、窗口归一化、可选 WHAM 刷新和 Session Details projection。
- 交付：`dd9ef954`、`466782c`；main 整合 `97930f7`、`6159644`、`b1770f9`；未 push。
- 验证：Python quota 17 passed、Session Details jsdom 13 passed；未做真实服务/browser E2E。

## T-008：带行号 Markdown 文件链接在 Editor 中打开

- 任务概括：支持带行号和行范围的 Markdown 文件链接在 Editor 中打开并定位到对应代码位置。

- 结论：支持 `#L42`、`#L42-L48`、`:42`、`:42-48`、URL 编码、Windows/UNC 和 `file://`，经 `pendingLocation` 与 Monaco 定位。
- 交付：来源 `4d3dae1`，main `cea2611`；未 push。
- 验证：定向 jsdom 8 passed、`git diff --check` 通过；未做真实服务/browser/mobile E2E。

## T-010：Codex Session 上下文窗口与压缩阈值

- 任务概括：让 Codex 上下文窗口与压缩阈值从设置贯通到新建、重启和待处理 Session，未设置时保持默认行为。

- 结论：SettingsPopover、Web/MCP/import/spawn 和 idle/pending restart 已接入；未设置时不传并恢复默认。
- 验证：adapter、Session、worker、SettingsPopover 定向测试与 build 通过；全量 lint 曾受既有 Hook 规则失败影响；未做 provider E2E。
- 交付：来源 `a9843c1`，随 React-only/附件整合进入 main；未 push。

## T-011：附件 Markdown 链接与附件下载

- 任务概括：统一安全的附件 Markdown 链接和受限下载行为，同时兼容历史附件文本与 Editor 文件链接。

- 结论：统一安全 Markdown 附件链接与受限下载 URL，兼容历史 `@"path"`，避免误判 Editor 文件链接。
- 交付：来源 `240d858`，冲突整合 `cd09841`，门禁修复 `4444371`；未 push。
- 验证：相关 Vitest 41 passed、build 和 ESLint 通过；未做真实服务/browser/mobile E2E。

## T-012：附件选择与 New Session 目录输入

- 任务概括：让附件选择和 New Session 目录输入复用一致的路径校验，避免过期或不存在目录被错误发送或创建失败。

- 结论：复用 `DirectoryInput`，服务端发送前重新枚举目录并阻止 stale path；New Session 可确认创建不存在目录。
- 验证：目录 pytest 17 passed、InputRow 28 passed、目录工具 12 passed、build/ESLint 通过；未做真实服务/browser/mobile E2E。
- 交付：来源/整合 `cd09841`，门禁修复 `4444371`；未 push。

## T-013：浏览器后台恢复前端状态

- 任务概括：让浏览器切回前台、页面恢复或 WebSocket 重连后重新取得权威 Session 状态，避免页面长期停在旧数据。

- 结论：visibility/pageshow/focus 与共享 WS 重连、权威状态刷新已接入。
- 验证：浏览器真实证据由 T-034 补充；其余定向测试和 build 以原交付报告为准。
- 交付：已合入 main；未 push。未验证范围见 T-034 详情。

## T-014：Memory 关闭时的 minimal requirements 分层

- 任务概括：在关闭 Memory 时保留轻量运行所需的最小依赖和行为，避免无关 Memory 能力改变基础任务执行。

- 结论：按 Memory 开关分层 minimal requirements，保持关闭时的轻量运行边界。
- 验证/交付：相关实现已合入 main；具体测试和历史提交保留在原交付报告，当前仅待开发者验收。

## T-015：Pan 通知、系统提醒与 msgBridge

- 任务概括：打通 Pan 的页面提示、浏览器通知、系统提醒和 msgBridge 通知链路，并明确各层的可观察行为与失败边界。

- 结论：通知、系统提醒和 msgBridge 链路已接入。
- 验证边界：Windows sender 返回 `windows_powershell_unavailable`；桌面 toast、Notification/msgBridge、reminder 到期仍有未验证项，T-034 已补充部分浏览器证据。
- 交付：已合入 main；未 push。

## T-016：Pan MCP 工具清单与 skill 同步审计

- 任务概括：核对 MCP 工具清单与 skill 文档的一致性，让可用工具、说明和实际入口不会互相脱节。

- 结论：MCP 工具清单与 skill 文档完成同步审计。
- 交付/验证：审计结果随 main 整合；无独立产品运行时证据，开发者验收由 overview 跟踪。

## T-017：搜索结果双击文件夹后搜索目录不更新

- 任务概括：修复从搜索结果双击文件夹后搜索目录仍指向旧位置的问题，使后续搜索使用用户刚选择的目录。

- 结论：修复搜索结果双击文件夹后的搜索目录更新。
- 交付/验证：实现已合入 main；保留原定向测试证据，真实浏览器验收按开发者验收批次处理。

## T-018：Windows Markdown 带行号链接打不开

- 任务概括：修复 Windows、UNC 和 file URI 的带行号文件链接解析，让点击后能在 Editor 打开并定位。

- 结论：修复 Windows/UNC/`file://` 带行号文件链接的解析与 Editor 定位；与 T-008 的通用链接能力协同。
- 交付/验证：已合入 main；T-034 覆盖盘符、相对路径和 `file://` 真实 Chromium 证据，UNC 仍列为未验证项。

## T-019：Codex Steer 在 Worker running 时可见性

- 任务概括：让 Codex Worker 运行中显示并支持 Steer 操作，使用户能向当前任务追加指令而不是误入普通队列。

- 结论：补齐 Worker running 时 Codex Steer 的可见性与 session-level endpoint。
- 验证边界：T-034 的真实运行证据中 Codex running Steer 尚未验证。
- 交付：已合入 main；未 push。

## T-020：Session Detail、System prompt 与 Codex quota

- 任务概括：完善 Session Details 的系统提示词、Usage 折叠和 Codex quota 展示，让运行配置与额度信息可查看。

- 结论：完成 Session Detail React #310、System prompt 折叠、Usage 折叠与 Codex quota projection；后续 UI 细节与队列语义由独立任务承接。
- 交付：main `c1a5ace`，祖先关系已核对；后续 T-020 不再阻塞原交付。
- 验证边界：Session Details 移动全屏是 T-035，真实运行证据由 T-034 补充。

## T-021/T-022：附件拖放 UI 与真实后端链路

- 任务概括：建立附件拖放 UI、后端上传和结构化输入的基础链路，为后续发送事务与 Session 隔离提供可验证入口。

- 结论：完成附件拖放 UI、后端链路和结构化输入的基础整合。
- 交付/验证：已合入 main；后续完整发送事务、路径投影和隔离语义由 T-027/T-027.1/T-027.2 承接。
- 备注：不在本条重复维护后续任务状态。

## T-024：Pan 解释器 config.json 配置与环境变量优先级

- 任务概括：统一 Pan 解释器来源优先级并贯通启动、MCP、Adapter 和后台生命周期，确保配置文件能稳定覆盖环境变量和默认值。

- 结论：支持 `config.json.python`，优先级为 `config.json.python > PAN_PYTHON > sys.executable`；覆盖 manifest、MCP descriptor、adapter、background runner、Windows 生命周期和 reload 边界。
- 交付：功能提交 `8b61ddba3ea880a3795e4ede1b68df5ef2838f51`，合并 `17632cc`；未 push。
- 验证：resolver/manifest/MCP/adapter/background/lifecycle/reload、compileall、JSON/PowerShell 检查通过；完整 pytest 有 1 个既有失败，QQ 测试缺少可选 nonebot，前端未执行。

## 待清理工作树清单（只标记，不执行清理）

> 本节是候选清单，不是删除授权。任何 worktree、branch、session、未跟踪报告、测试或运行证据均未被删除；清理前仍需重新核对现场和所有权。

### 已被 `cba9df0` 集成或由更好方案覆盖：待清理候选

- `D:\project\pan-worktrees\frontend-consistency-repair-ds-20260922`：detached `3f1b42e`，clean；`782b16e`→`3f1b42e` 提交链均为 `cba9df0` 祖先，修复/协议红测内容已进入选择性整合链。仅标记待清理，不删除。
- `D:\project\pan-worktrees\frontend-consistency-verify-glm-20260922`：detached `2205bcd`，clean；`2205bcd` 与 `782b16e` 均为 `cba9df0` 祖先，验证树无额外 dirty/untracked 现场。仅标记待清理，不删除。
- `D:\project\pan-worktrees\frontend-integration-deepseek-20260921`：branch `feature/frontend-integration-deepseek-20260921`，HEAD `b324aa1`，clean；该唯一代码提交不是 `cba9df0` 祖先，但其 patch-id 与集成提交 `44b9ccd` 相同，已被选择性方案覆盖。仅标记待清理，不删除。

### 必须保留：仍有独立证据或未跟踪调查材料

- `D:\project\pan-worktrees\frontend-consistency-followup-glm-20260922`：HEAD `a6a2ed5` 虽为 `cba9df0` 祖先，但仍有 93 项未跟踪 followup E2E、Session 和运行证据；保留，不能标为可清理。
- `D:\project\pan-worktrees\frontend-reaudit-astra-20260921`：main HEAD `591367a`，有 3 项未跟踪执行方案/探针/证据；其实施方案是修复链输入，保留。
- `D:\project\pan-worktrees\frontend-reaudit-browser-ds-20260921`：main HEAD `591367a`，有 2545 项未跟踪 Chromium、附件、Session 和 trace 证据；保留，不能因 cba 的 strict E2E 通过而清理原始复现材料。
- `D:\project\pan-worktrees\frontend-reaudit-history-ds-20260921`：main HEAD `591367a`，有 8 项未跟踪历史/queue/ordered-events 调查报告与正式红测；保留，作为独立调查输入和回归证据。
- `D:\project\pan-worktrees\frontend-reaudit-protocol-ds-20260921`：main HEAD `591367a`，有 40 项未跟踪协议报告、探针和 pytest 证据；真实 provider 未调用，Steer receipt 等协议缺口仍未闭合，保留。
- `D:\project\pan-worktrees\be3-eventloop-e2e-deepseek-20260921`：BE-3 的完整独立 HTTP/WS、120k 冷读和进程清理证据仍有独立验收价值；`94b62e3` 只选择性覆盖其代码范围，不标为可清理。
