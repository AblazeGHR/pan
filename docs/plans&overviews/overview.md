# Pan 项目总览

> 维护者：Pan SMA
> 更新日期：2026-09-12
> 本文只做项目阶段和功能索引：每个功能集中记录计划、调查结论、实现状态、工作树、测试和是否合入 main。长期约束、验收口径和编排记忆见 [constraints-and-acceptance.md](constraints-and-acceptance.md)。

## 一、当前阶段

当前处于“主分支功能整合 + Codex 能力扩展 + React-only 前端收敛”阶段。

| 分支 | 路径 | HEAD | 阶段状态 |
|---|---|---|---|
| `main` | `D:\project\Pan-main` | `0b23a86`（通知/msgBridge 已本地合入，未 push） | 当前集成基线；工作树有用户未提交文档修改 |
| `practical` | `D:\project\Pan` | `46d5879` | 与 main 同一当前基线，clean；保留用户脚本修改 |

## 二、已合入 main 的改动

### main 与 practical 当前差异（本节置顶）

- `main`（代码基线 `5d1f214`，未 push）比 `practical`（`46d5879`）多：Toast 点击复制（`31b792b`、`8119bf3`）、Codex 全局额度缓存（`97930f7`、`6159644`、`b1770f9`）、MCP model_list adapter 发现（`0e431e5`）、Markdown 行号链接跳转（`cea2611`）、React-only/Vanilla 退役（`5ffd395c`、`2947d0c`）、Codex 上下文设置（`a9843c1`）、附件 Markdown/目录输入整合（`240d858`、`9c0852b`、`cd09841`、`4444371`）、Memory 关闭时 minimal requirements 分层（`421c591`）、浏览器后台恢复（`5d1f214`）。

### 1. Session prompt 拆分与非递归交接准备

- 计划/目标：将 Session JSON 的 `system_prompt` 拆为 `original_prompt` 与 `handoff_prompt`，计算兼容的 `systemPrompt`，避免旧简报在后续交接中递归叠加。
- 实现：持久化字段、旧 JSON 兼容、worker/HTTP/MCP/导入/分支路径均分别传递原始提示和本次简报；不猜测拆分历史混合文本。
- 合入：main `bf73b5b`；practical 中的承接提交为 `77a2e66`。
- 状态：已合入 main；真实 handoff 按安全边界未执行。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [ ] 无需基础测试或测试通过
  - [ ] 兼容性问题、回归问题检验
- 合入/push 状态：合入 main：是（依据：`bf73b5b`）；push：未核对。
### 2. Session Details / Rename / Usage / System prompt UI

- 计划/目标：完善 Session Details、rename、Usage、Codex quota 和可折叠 System prompt 展示。
- 实现：Session name 复制；rename 预填原名并全选；Usage 默认折叠；Codex 只显示实际存在的周/月额度；System prompt 默认折叠、展开保留换行；不存在的额度直接省略；保留 ChatMessages 底部跟随行为。
- 主分支整合：`f497d35196e7bbddde3aee564619871acc277edc`，以当前 main 为基线手工整合，未机械覆盖旧 feature 分支。
- 历史工作树：`D:\project\pan-worktrees\session-detail-usage-rename-20260909`，旧分支 `feature/session-detail-usage-rename-20260909`，最终 `e716f617`。
- 验证：main 整合后全量 Vitest `46 files / 407 tests passed`，lint 0 errors，build 通过；未做真实浏览器/mobile E2E。
- 状态：已合入 main。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [ ] 无需基础测试或测试通过
  - [ ] 无需 e2e 或 e2e 通过
  - [ ] 兼容性问题、回归问题检验
- 合入/push 状态：合入 main：是（依据：`f497d35196e7bbddde3aee564619871acc277edc`）；push：未核对。

### 3. ChatMessages 底部跟随与 Scroll to bottom

- 计划/目标：到达底部时隐藏按钮，并在接近底部时跟随新消息但不吸附正在翻阅历史的用户。
- 实现结论：底部距离 `<=48px` 视为跟随区并隐藏按钮；超过 `48px` 显示按钮且不自动吸附；保留内容增长前几何快照、分页和会话切换处理。
- 合入：main `51a159c`，来源 practical `1f05127`。
- 验证：前端相关及全量测试曾通过；真实浏览器/mobile 滚动物理行为未做 E2E。
- 状态：已合入 main。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [ ] 无需基础测试或测试通过
  - [ ] 无需 e2e 或 e2e 通过
  - [ ] 兼容性问题、回归问题检验
- 合入/push 状态：合入 main：是（依据：`51a159c`）；push：未核对。

### 4. Windows 窄编码日志兼容与 CI

- 计划/目标：修复 GitHub Windows runner 的 cp1252 stdout 无法输出 Unicode 箭头导致隔离 HTTP E2E readiness 失败。
- 实现结论：日志输出对 stdout 编码做兼容处理，无法编码字符使用 `backslashreplace`；增加回归测试；CI Python 版本/测试配置同步调整。
- 合入：`c734d6a`；后续 CI 提交 `31e1ee3` 已在 main 历史。
- 验证：本地复现并修复原失败；Python workflow 测试、前端 Vitest、lint、build 均曾通过。
- 状态：已合入 main。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [ ] 无需基础测试或测试通过
   - [ ] 兼容性问题、回归问题检验
- 合入/push 状态：合入 main：是（依据：`c734d6a`、`31e1ee3`）；push：未核对。

### 5. practical 启动脚本与 MCP 依赖检查

- 计划/目标：让启动脚本正确检查 Pan Core 与 stdio MCP 的运行依赖，同时保留用户脚本修改。
- 实现：依赖检查和 MCP 启动环境修复。
- 合入：`9138a79`。
- 状态：已合入 main；practical 用户脚本仍需保留，不可覆盖。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [ ] 无需基础测试或测试通过
  - [ ] 兼容性问题、回归问题检验
- 合入/push 状态：合入 main：是（依据：`9138a79`）；push：未核对。

### 6. Toast 点击复制

- 计划/目标：React Toast 主体点击复制展示文本，同时保留 dismiss；支持 Enter/Space，关闭按钮独立工作并处理 clipboard 失败。
- 实现：`Toast.tsx` 与 `Toast.test.tsx`；Toast 外层为 `role="alert"`，复制主体与关闭按钮为同级交互元素；clipboard 失败不会破坏 dismiss。
- 工作树/分支：历史工作树 `D:\project\pan-worktrees\toast-click-copy-20260911`，分支 `feature/toast-click-copy-20260911`；当前工作树 clean。该分支的 `bc805af` 仅是测试查询修正，main 已有等价修正，不再单独合入。
- 提交：功能 `0175268`；已合入 main 的对应提交为 `31b792b`、`8119bf3`。
- TA/任务：`ses_cdf7519dda1ca097`；TA done；SMA 已完成静态验收。
- 测试/未验证项：在当前 `Pan-main` 实际运行 `pnpm exec vitest run --environment jsdom src/components/ui/Toast.test.tsx`，`1 file / 6 tests passed`；`git diff --check` 通过；未做真实浏览器/mobile E2E，完整 build/lint 未验证。
- 状态：功能代码与测试修正已合入 main；尚未开发者验收。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [ ] 无需基础测试或测试通过
  - [ ] 无需 e2e 或 e2e 通过
  - [ ] 兼容性问题、回归问题检验
- 合入/push 状态：合入 main：是（`31b792b`、`8119bf3`）；push：否。

### 7. Codex 全局额度缓存

- 计划/目标：额度属于 Codex profile/账号而非 Session；Worker 离线时仍可查看最近额度，并为后续主动查询保留扩展点。
- 实现：`data/codex/quota/<profile-key>.json` 全局缓存；app-server push 持久化；profile 隔离；离线 API；窗口时长归一化；可选 WHAM 刷新；Session Details 使用 usage API projection。
- 来源工作树/分支：`D:\project\pan-worktrees\codex-quota-cache-luna-20260909` / `feature/codex-quota-cache-luna-20260909`；历史提交 `dd9ef954`、`466782c`。
- main 整合：`97930f7`、`6159644`，冲突修正 `b1770f9`；保留 main 原有 System prompt 与在线 Worker 快照逻辑，离线时使用缓存。
- TA/任务：`ses_df2a15acf46365e3`；TA done；SMA 已完成整合验收。
- 测试/未验证项：Python quota 相关测试 `17 passed`；Session Details 定向 jsdom `13 passed`；未做真实服务/浏览器 E2E。
- 状态：功能已合入 main；尚未开发者验收。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [ ] 无需基础测试或测试通过
  - [ ] 无需 e2e 或 e2e 通过
  - [ ] 兼容性问题、回归问题检验
- 合入/push 状态：合入 main：是（`97930f7`、`6159644`、`b1770f9`）；push：否。

### 8. MCP `model_list` adapter 发现改进

- 计划/目标：没有 adapter 参数时不再静默选择 CBC，先返回可用 adapter；指定 adapter 后再查询其模型列表。
- 实现结论：空 adapter 返回 `adapter_required`、`availableAdapters`、调用提示；未知 adapter 返回可行动错误；指定 `codex` 返回 Codex 模型；不逐个请求所有 adapter。
- 来源工作树/提交：独立仓库 `D:\project\pan-worktrees\mcp-model-list-adapter-discovery-20260909`，来源提交 `e66613e6`；main 整合提交 `0e431e5`。
- TA/任务：历史 TA 已完成报告；SMA 已完成补丁迁移与静态验收。
- 测试/未验证项：`py_compile`、`git diff --check` 通过；定向 pytest 因当前环境缺少 `mcp` 包，在收集阶段阻塞；未做 live MCP E2E。
- 状态：功能与文档已合入 main；尚未开发者验收。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [ ] 无需基础测试或测试通过
  - [ ] 无需 e2e 或 e2e 通过
  - [ ] 兼容性问题、回归问题检验
- 合入/push 状态：合入 main：是（`0e431e5`）；push：否。

### 9. 带行号 Markdown 文件链接在 Editor 中打开

- 计划/目标：解析带行号的 Markdown 文件链接，并在 Editor 中真实定位到行或行范围；普通文件链接保持兼容，非法行号安全降级。
- 实现：支持 `path#L42`、`path#L42-L48`、`path:42`、`path:42-48`，以及 URL 编码、Windows/UNC、`file://` 路径；通过 `pendingLocation` 和 Monaco 定位实现真实跳转。
- 来源工作树/分支：`D:\project\pan-worktrees\markdown-file-link-line-20260911` / `feature/markdown-file-link-line-20260911`；来源提交 `4d3dae1`，当前代码整合提交 `cea2611`，未迁入来源工作树的旧 overview 修改。
- TA/任务：编排 `ses_714459018a727d91`，实际执行 TA `ses_042bcc8f0e724bf3`；TA done；SMA 已完成整合验收。
- 测试/未验证项：MarkdownRenderer 定向 jsdom `8 passed`；`git diff --check` 通过；未做真实服务/浏览器/mobile E2E。
- 状态：功能与测试已合入 main；尚未开发者验收。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [ ] 无需基础测试或测试通过
  - [ ] 无需 e2e 或 e2e 通过
  - [ ] 兼容性问题、回归问题检验
- 合入/push 状态：合入 main：是（`cea2611`）；push：否。

### 10. Vanilla 前端彻底移除与归档

- 实现：删除旧 Vanilla 源码、HTML/CSS/构建入口和 `/vanilla` 服务路由；React 是唯一前端。根路径重定向 `/react/`；React dist 缺失时 `/` 与 `/react/` 都返回可操作 503，`/vanilla` 为 404；历史材料保留在 archive。
- 来源/整合：来源 `5ffd395c`；React dist 缺失回归修复 `2947d0c`；已经隔离整合分支验证后合入本次 main 整合。
- 验证：`tests/test_web_frontend.py` 4 passed、`tests/test_config_reload.py` 19 passed；完整 `pytest tests/ packages/qq -q` 跑至 100%，仅 `test_codex_quota_api.py` 的 docstring 断言失败，已在合入前 `main@cea2611` 同一 venv 原样复现；`git diff --check`、compileall 通过；未做真实服务/浏览器 E2E。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
- 合入/push 状态：合入 main：是（本次本地整合）；push：否。

### 11. Codex Session 上下文窗口与压缩阈值设置

- 实现：SettingsPopover 的 More 区可编辑 `model_context_window` 与 `model_auto_compact_token_limit`；未设置时不传，恢复默认删除 Session key；Web/MCP/导入、spawn `-c` 参数、idle respawn/running pending restart 已接入。
- 来源/整合：来源 `a9843c1`；本次与 React-only、附件改动共同通过隔离整合后合入 main。
- 验证：Codex adapter 2 passed、Session validation 8 passed、worker branch 1 passed；SettingsPopover 定向 Vitest 2 passed、`pnpm build` 通过。本次新增文件 ESLint 通过；全量 lint 仍被 main 既有 `SessionDetailsModal.tsx:134` Hook 规则错误阻断；未做真实 provider E2E。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
- 合入/push 状态：合入 main：是（本次本地整合）；push：否。

### 12. 附件 Markdown 链接与附件下载改造

- 实现：附件统一生成安全标准 Markdown 链接；客户端上传与服务端附件均通过受限下载 URL；兼容历史 `@"path"`，附件路由不会被当作 Editor 文件链接。
- 来源/整合：来源 `240d858`，与目录输入改造的冲突经整合提交 `cd09841` 解决，后续门禁修复 `4444371`；本次合入 main。
- 验证：附件 Markdown/MarkdownRenderer/InputRow 定向 Vitest 合计 41 passed；相关后端目录/下载验证纳入条目 13；`pnpm build` 和本批 ESLint 通过；未做真实服务/浏览器/mobile E2E。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
- 合入/push 状态：合入 main：是（本次本地整合）；push：否。

### 13. 附件选择与 New Session 目录输入统一改造

- 实现：复用 `DirectoryInput`，以最后一个分隔符划分目录/检索文本；服务端附件发送前重新枚举父目录，非法或失效路径阻止 enqueue；New Session 目录不存在时经 Modal 确认创建。
- 来源/整合：来源 `9c0852b`；与附件 Markdown 改造联合整合提交 `cd09841`，门禁修复 `4444371`；本次合入 main。
- 验证：`tests/test_web_directory_picker.py` 17 passed；InputRow 定向 Vitest 28 passed（含编辑器附件路径重新枚举与 stale path 阻止发送）；目录工具测试 12 passed；`pnpm build` 和本批 ESLint 通过；未做真实服务/浏览器/mobile E2E。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
- 合入/push 状态：合入 main：是（本次本地整合）；push：否。

### 14. 浏览器后台恢复前端状态

- 计划/目标：页面由后台节能、bfcache 或失焦恢复可见时，去重触发安全 WS 恢复，并刷新权威 sessions、workers、当前 history、queue 与 interactive 请求；保留草稿、滚动和本地状态，不整页 reload。
- 调查结论：`wsClient` 是共享单例，不能因页面事件调用 `disconnect()`；已有 `loadSessions`/`selectSession` 的序列保护可复用，但恢复 history 需要额外按 session 的单飞序列，防止旧请求覆盖新 session。
- 实现：`wsClient` 增加连接活动时间、新鲜度判断和不移除订阅者的 `reconnect()`；新鲜度仅由 open/服务端消息更新，heartbeat 发送不伪造活跃；`useWebSocket` 集中监听 `visibilitychange`、`pageshow`、`focus`，100ms debounce + single-flight；陈旧连接交由 open 路径完成刷新，健康连接刷新 sessions/workers/current history/queue 并同步 interactive；mock mode 保持不建真实 WS。
- 来源工作树/分支：`D:\project\pan-worktrees\web-resume-on-focus-20260911` / `feature/web-resume-on-focus-20260911`，基于 `main@65f82b0`；来源提交 `a25a123`、修正提交 `77dd322`，本地合入提交 `5d1f214`；来源工作树 clean。
- TA/任务：`ses_151c0fd646c12633`；任务 done；SMA 已完成独立复核。
- 测试/未验证项：`pnpm exec vitest run src/hooks/useWebSocket.test.tsx src/stores/sessionStore.race.test.ts`：2 files / 52 tests passed；`pnpm run build` 通过；涉及文件 ESLint 通过；未做真实 browser visibility/bfcache、服务/WS/provider E2E。
- 状态：已实现并合入 main；尚未开发者验收。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [x] 基础测试通过
  - [ ] browser/mobile E2E 通过
  - [x] 竞态与 mock mode 回归已由 jsdom 用例覆盖
- 合入/push 状态：合入 main：是（本地 `main@5d1f214`）；push：否。

### 15. Memory 关闭时的 minimal requirements 分层

- 调查结论：Core + MCP 运行时直接需要 `httpx`；`mcp`、`fastapi`、`uvicorn`、`websockets`、`psutil` 保持运行依赖；`pytest` 属于 dev/test，不应作为纯运行时 minimal 依赖；Memory ML 链保持 optional。
- 实现：`minimal-requirements.txt` 仅含 Core/API/MCP 运行依赖；新增 `dev-requirements.txt`（基于 minimal，含 pytest/pytest-timeout）与 `memory-requirements.txt`（Memory provider/索引可选层）；根 `requirements.txt` 保留为兼容聚合入口，QQ 仍由 `packages/qq/requirements.txt` 独立提供。`scripts/setup.bat`/`setup.sh` 的 Core 步骤使用 minimal，`start_pan.bat`/setup 探测覆盖 `httpx` 与 `mcp.server.fastmcp`，缺失时给出修复命令；Memory=false 的 provider 导入边界由回归测试锁定。
- 来源工作树/分支：`D:\project\pan-worktrees\minimal-requirements-memory-off-20260911` / `feature/minimal-requirements-memory-off-20260911`，基于 `main@65f82b0`；实现提交 `37199dc`，本地合入提交 `421c591`；来源工作树 clean。
- 测试/未验证项：`D:\project\Pan\.venv\Scripts\python.exe -m pytest tests/test_dependency_layers.py tests/test_cbc_import_guard.py tests/test_mcp_integration.py tests/test_memory_search.py -q`：60 passed；指定解释器实际导入 Core/API/MCP 链成功。`pytest-timeout` 未安装，因此该运行显示 pytest 配置 warning；另有既有 pydantic forward-reference warning。未安装根 `requirements.txt`，未启动服务或执行真实 API/MCP/browser E2E。
- 状态：已实现并合入 main；尚未开发者验收。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [x] 基础依赖分层静态/定向测试通过
  - [ ] 真实 API/MCP 或 browser E2E 通过
- 合入/push 状态：合入 main：是（本地 `main@421c591`）；push：否。

### 16. Pan 通知、系统提醒与 msgBridge

- 计划/目标：为浏览器/系统通知统一强制 `Pan:` 前缀；持久化 Session 完成通知开关；提供可恢复、一次性绝对 dueAt 系统提醒；以 msgBridge 替换 UI 旧 Postbox 名称，同时保留 QQ 标签页。
- 调查/设计结论：复用 Session PATCH 与 `worker.result`；`agent_notify` 保持内部 agent queue 语义。旧 Session JSON 的 `notification_settings` 缺失时安全默认为关闭；handoff 与 native branch 复制该设置，`copySettings=false` 默认关闭。系统 sender 采用可注入接口，Windows 使用 inbox PowerShell/.NET `NotifyIcon` 子进程，标题/body 作为独立参数传递；非 Windows 明确返回 `unsupported_system_notification`，Windows 子进程失败返回诊断错误，通知失败不影响任务完成。
- 实现：`packages/core/notifications.py`、`packages/core/reminders.py`；Session/worker/Web/MCP 与 React msgBridge UI 已接入。MCP 工具为 `notification_send`、`reminder_register`、`reminder_list`、`reminder_cancel`，均要求 Pan 注入身份并经过 `_check_access`；`agent_notify` 未改义。提醒 loop 已抽取可测试的 claim-before-send 单轮执行。
- 来源/整合：feature `1033582`，Windows encoded PowerShell 前导修复 `5406953`；integration merge commit `0b23a86`。来源与整合 worktree 均未触碰 `D:\project\Pan-main` 的 `docs/references/cli-adapter-special-behaviors.md` 与 `docs/developLog.md`，两者原有用户修改已保留。
- TA/session：`ses_b050dffa6d3fa226`、`ses_57a57c3cf407782a`、`ses_79800ea706f8a813`；任务状态：done；SMA 静态检查：通过。
- 测试/未验证项：当前整合内容执行 `D:\project\Pan\.venv\Scripts\python.exe -m pytest -q tests/test_notifications_reminders.py tests/test_session_schema_compat.py tests/test_session_handoff.py tests/test_mcp_isolation.py tests/test_worker_states.py tests/test_worker_output_mode.py tests/test_web_frontend.py`：95 passed；`compileall -q packages tests` 通过。隔离 worktree 自有依赖下前端通知相关 Vitest 5 files/68 tests passed，`tsc -b`、`vite build`、相关 ESLint 通过。真实 Windows PowerShell sender 进程返回 0；真实 FastAPI/API、live MCP、granted browser worker.result Notification、reminder claim/deliver 与 raw `/ws` frame 均通过；Chromium 默认权限在当前环境为 `denied`，无法验证 `default`，桌面 Toast 可见性未由截图确认。既有标准 browser harness 仍为 3 项通过、exact-bottom 1 项基线失败；全量 Vitest/lint 的既有基线失败仍未处理。未操作 8768；provider/mobile E2E 未做。
- 状态：已实现并合入 main；尚未开发者验收。
- 验收清单：
  - [x] 合入 main
  - [ ] 开发者验收
  - [x] 定向基础测试通过
  - [ ] 全量门禁无基线失败
  - [ ] 完整 OS/browser/provider E2E
- 合入/push 状态：合入 main：是（本地 `main@0b23a86`）；push：否。

## 三、已完成但尚未合入 main 的改动

当前暂无。

## 四、正在进行的任务/改动

### Pan MCP 工具清单与 skill 同步审计

- 计划/目标：以 `packages/mcp/server.py` 的 `@mcp.tool()` 为事实源，保持 Pan skill 工具名、参数和关键语义同步，并区分 `worker_*` 兼容别名与一等工具。
- 调查/实现：确认 Pan server 为 49 个工具（42 个一等工具、7 个 `worker_*` 兼容别名）；`pan-qq` 为独立 server 的 7 个工具。更新主源及 HTTP 引用，新增 stdlib-only `scripts/check_pan_skill_tools.py`。
- 工作树/分支：`D:\project\pan-worktrees\pan-skill-tool-sync-20260912`；detached HEAD，基于 `main@0407dbf8e5e899530a001c5d1e1eafc82e1d4315`；提交后 clean。
- 提交：`c5239c41f57b113ed6cf167b144f172c760f504a`；本批 integration 分支已整合，待合入 main，未 push。
- 验证：静态工具集合检查通过；`py_compile` 通过；定向 pytest `28 passed, 1 skipped`；`git diff --check` 通过。未启动服务，未访问 8765/8767/8768。
- 同步副本：本 worktree 不存在 `.codebuddy/skills/pan/SKILL.md`；安装目录副本未修改，仍需在允许外部文件变更时另行同步。
- 验收清单：
  - [ ] 合入 main
  - [ ] 开发者验收

### Bug：搜索结果双击文件夹后搜索目录不更新

- 现象：New Session 的目录搜索结果中双击文件夹，第一次 click 先执行目录选择，路径停留在父目录搜索结果，无法进入目标目录。
- 根因/修复：目录按钮延迟单击选择 250ms；双击取消待执行选择并将输入更新为 `path\\`，由 `parseDirectoryInput` 触发目标目录请求；文件点击保持立即选择。
- TA/session：`ses_84d74859358ed744`；工作树 `D:\project\pan-worktrees\pan-directory-input-doubleclick-20260912`。
- 提交：`d793083165f7d7b3b9bb27a7b71a4ddf8d8b4499`；本批 integration 分支已整合，待合入 main，未 push。
- 验证：目录双击回归、目录工具、InputRow 定向 Vitest，`tsc --noEmit`、改动文件 ESLint、`git diff --check` 通过；NewSessionModal 仍有 4 个既有 `Session 1`/`session-1` 断言失败。
- 验收清单：
  - [ ] 合入 main
  - [ ] 开发者验收
  - [x] 定向测试通过
  - [ ] 真实浏览器/服务 E2E

### Bug：Windows Markdown 带行号链接打不开

- 现象：链接 `/D:/project/Pan-main/docs/plans&overviews/overview.md:20` 被当作 `/D:/...` 根路径，打开文件时报 `Not a file`。
- 根因/修复：Markdown 文件链接归一化没有识别“单个前导斜杠 + Windows 盘符”；仅移除 `/D:/` 形式的前导斜杠，保留合法 Unix rooted path、UNC、file URL 和既有行号格式。
- TA/session：`ses_8b78dbec02f828cf`；工作树 `D:\project\pan-worktrees\pan-markdown-windows-line-link-20260912`。
- 提交：`051018fe2b6910bc9c05440e194d700776717601`；本批 integration 分支已整合，待合入 main，未 push。
- 验证：真实 `MarkdownRenderer` 点击链路与 Windows href 回归、Unix/UNC 兼容测试；13 tests passed，`tsc -b`、改动文件 ESLint、`git diff --check` 通过。未做真实服务/磁盘 E2E。
- 验收清单：
  - [ ] 合入 main
  - [ ] 开发者验收
  - [x] 定向测试通过
  - [ ] 真实浏览器/服务 E2E

### Bug：Codex Steer 按钮在 Worker running 时偶尔消失

- 现象：Worker 实际 running，但前端因 session summary 快照暂时落后，Steer 控件消失。
- 根因/修复：`InputRow` 改用同一 session 的 `workerStore` live running 状态和 session-level steer endpoint，不再依赖 stale `currentSession.workerStatus/workerId`。
- TA/session：`ses_f3099786573947c4`；工作树 `D:\project\pan-worktrees\pan-steer-visibility-20260912`。
- 提交：`8f69c96`；尚未合入 main，未 push。
- 验证：相关 Vitest 99 passed，`tsc -b`、Python worker branch 2 passed、`git diff --check` 通过；未做真实浏览器/API E2E。
- 验收清单：
  - [ ] 合入 main
  - [ ] 开发者验收
  - [x] 定向测试通过
  - [ ] 真实浏览器/API E2E

### Bug：点击 Session Detail 出现 Minified React error #310

- 现象：用户点击 Session Detail 时出现 React #310；是否与旧 Session 数据兼容尚未确认。
- 当前结论：尚无复现日志、组件堆栈或兼容性证据，不能先归因于旧 Session；待独立调查。
- 状态：未形成修复提交，尚未纳入本批合并。
- 验收清单：
  - [ ] 完成复现与根因确认
  - [ ] 修复并补回归测试
  - [ ] 开发者验收

## 五、计划要做的任务

当前暂无独立列出的计划项；新计划和下一步建议直接写入对应功能条目。
