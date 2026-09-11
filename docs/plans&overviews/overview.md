# Pan 项目总览

> 维护者：Pan SMA
> 更新日期：2026-09-11
> 本文只做项目阶段和功能索引：每个功能集中记录计划、调查结论、实现状态、工作树、测试和是否合入 main。长期约束、验收口径和编排记忆见 [constraints-and-acceptance.md](constraints-and-acceptance.md)。

## 一、当前阶段

当前处于“主分支功能整合 + Codex 能力扩展 + UI 目录交互改造”阶段。

| 分支 | 路径 | HEAD | 阶段状态 |
|---|---|---|---|
| `main` | `D:\project\Pan-main` | `4aa30a2` | 当前集成基线，clean |
| `practical` | `D:\project\Pan` | `1f05127` | 较早的 practical 基线，clean；保留用户脚本修改 |

## 二、已合入 main 的功能

### main 与 practical 当前差异（本节置顶）

- `main` 当前为 `4aa30a2`，`practical` 当前为 `1f05127`；按当前 Git 历史，`practical` 没有领先 `main` 的独有提交，main 包含后续集成提交。
- main 相对 practical 现已包含：prompt 拆分、Session usage/rename/UI 整合、ChatMessages 48px 底部跟随、Windows 窄编码日志兼容、CI 调整，以及本总览和原生 Codex 调查记录。
- practical 仍是服务工作 checkout，保留用户对 `scripts/setup.bat`、`scripts/start_pan.bat` 的未提交修改；不得把 main 的文档或功能提交反向覆盖这些用户修改。
- 当前未合入 main 的功能不因为存在于其他工作树或 practical 相关历史中而视为已集成。

### 1. Session prompt 拆分与非递归交接准备

- 计划/目标：将 Session JSON 的 `system_prompt` 拆为 `original_prompt` 与 `handoff_prompt`，计算兼容的 `systemPrompt`，避免旧简报在后续交接中递归叠加。
- 实现：持久化字段、旧 JSON 兼容、worker/HTTP/MCP/导入/分支路径均分别传递原始提示和本次简报；不猜测拆分历史混合文本。
- 合入：main `bf73b5b`；practical 中的承接提交为 `77a2e66`。
- 状态：已合入 main；真实 handoff 按安全边界未执行。

### 2. Session Details / Rename / Usage / System prompt UI

- 计划/目标：完善 Session Details、rename、Usage、Codex quota 和可折叠 System prompt 展示。
- 实现：Session name 复制；rename 预填原名并全选；Usage 默认折叠；Codex 只显示实际存在的周/月额度；System prompt 默认折叠、展开保留换行；不存在的额度直接省略；保留 ChatMessages 底部跟随行为。
- 主分支整合：`f497d35196e7bbddde3aee564619871acc277edc`，以当前 main 为基线手工整合，未机械覆盖旧 feature 分支。
- 历史工作树：`D:\project\pan-worktrees\session-detail-usage-rename-20260909`，旧分支 `feature/session-detail-usage-rename-20260909`，最终 `e716f617`。
- 验证：main 整合后全量 Vitest `46 files / 407 tests passed`，lint 0 errors，build 通过；未做真实浏览器/mobile E2E。
- 状态：已合入 main。

### 3. ChatMessages 底部跟随与 Scroll to bottom

- 计划/目标：到达底部时隐藏按钮，并在接近底部时跟随新消息但不吸附正在翻阅历史的用户。
- 实现结论：底部距离 `<=48px` 视为跟随区并隐藏按钮；超过 `48px` 显示按钮且不自动吸附；保留内容增长前几何快照、分页和会话切换处理。
- 合入：main `51a159c`，来源 practical `1f05127`。
- 验证：前端相关及全量测试曾通过；真实浏览器/mobile 滚动物理行为未做 E2E。
- 状态：已合入 main。

### 4. Windows 窄编码日志兼容与 CI

- 计划/目标：修复 GitHub Windows runner 的 cp1252 stdout 无法输出 Unicode 箭头导致隔离 HTTP E2E readiness 失败。
- 实现结论：日志输出对 stdout 编码做兼容处理，无法编码字符使用 `backslashreplace`；增加回归测试；CI Python 版本/测试配置同步调整。
- 合入：`c734d6a`；后续 CI 提交 `31e1ee3` 已在 main 历史。
- 验证：本地复现并修复原失败；Python workflow 测试、前端 Vitest、lint、build 均曾通过。
- 状态：已合入 main。

### 5. practical 启动脚本与 MCP 依赖检查

- 计划/目标：让启动脚本正确检查 Pan Core 与 stdio MCP 的运行依赖，同时保留用户脚本修改。
- 实现：依赖检查和 MCP 启动环境修复。
- 合入：`9138a79`。
- 状态：已合入 main；practical 用户脚本仍需保留，不可覆盖。

## 三、正在实现的功能

### 1. Codex Session 上下文窗口与压缩阈值设置

- 计划/目标：在发送框旁的 Settings 中，Codex permission mode 后增加默认折叠的 More 区域，编辑 `model_context_window` 与 `model_auto_compact_token_limit`；默认不传值；提供恢复默认按钮。
- 原生 Codex 调查结论：已创建 thread 使用 `codex exec resume` 追加两个 `-c` 参数成功，thread ID 不变；`model_context_window` 运行事件由 baseline `258400` 变为 `60800`；不要求新建 thread。压缩阈值参数接受但未触发验证。
- 生命周期决策：按现有 model/effort 等进程相关设置处理——idle 自动 respawn，running 设置 `pending_restart`，任务结束回 idle 后自动 respawn，无 Worker 时下一次 spawn 生效。
- 当前实现工作树：`D:\project\pan-worktrees\codex-context-settings-luna-20260911`。
- 分支：`feature/codex-context-settings-luna-20260911`，基线 main `4aa30a2`。
- TA：`ses_ced6387755a609a5`，Codex `gpt-5.6-luna` high。
- 状态：实现中，尚未提交、验收或合入 main。

### 2. 附件选择与 New Session 目录输入统一改造

- 计划/目标：附件服务端选择窗口取消独立搜索目录；以输入最后一个 `\\` 前的目录为检索基准、之后为检索文本；非法目录显示“当前目录非法”；最终提交再次校验并阻止非法提交。New Session 复用同样规则，目录不存在时询问是否创建。
- 设计重点：共享路径解析、存在性校验、检索和创建确认逻辑；覆盖 Windows 盘符、根目录、末尾反斜杠、空输入、权限/安全边界和提交前二次校验。
- 当前实现工作树：`D:\project\pan-worktrees\attachment-directory-input-luna-20260911`。
- 分支：`feature/attachment-directory-input-luna-20260911`，基线 main `4aa30a2`。
- TA：`ses_560ae06acbb82693`，Codex `gpt-5.6-luna` high。
- 状态：实现中，尚未提交、验收或合入 main。

## 四、已完成但尚未合入 main 的功能

### 1. Codex 全局额度缓存

- 计划/目标：额度属于 Codex profile/账号而非 Session；Worker 离线时仍可查看最近额度，并为后续主动查询保留扩展点。
- 实现：`data/codex/quota/<profile-key>.json` 全局缓存；app-server push 持久化；profile 隔离；离线 API；窗口时长归一化；可选 WHAM 刷新；Session Details 使用 usage API projection。
- 工作树：`D:\project\pan-worktrees\codex-quota-cache-luna-20260909`。
- 分支/提交：`feature/codex-quota-cache-luna-20260909` @ `466782c209edfe29205c3e5e8388ce046cd5e899`，父提交 `dd9ef954`。
- 验证：Python quota `97 passed`；全量 Vitest `46 files / 404 tests passed`；lint 0 errors；build、compileall、diff check 通过；未做真实服务/浏览器 E2E。
- 状态：实现完成，待主仓库集成审查；未合入 main，未 push。

### 2. MCP `model_list` adapter 发现改进

- 计划/目标：没有 adapter 参数时不再静默选择 CBC，先返回可用 adapter；指定 adapter 后再查询其模型列表。
- 实现结论：空 adapter 返回 `adapter_required`、`availableAdapters`、调用提示；未知 adapter 返回可行动错误；指定 `codex` 返回 Codex 模型；不逐个请求所有 adapter。
- 工作树：`D:\project\pan-worktrees\mcp-model-list-adapter-discovery-20260909`（该目录是独立 Git 仓库，尚未纳入父仓库 worktree 拓扑）。
- 提交：`e66613e6fa4db7578381a5475a8b66193c2bf863`。
- 验证：`55 passed, 1 skipped`；compileall、diff check 通过；未做 live MCP E2E。
- 状态：报告完成，需先核对仓库基线和补丁再集成；未合入 main，未 push。

## 五、已完成调查但尚未实现的功能

### Memory 关闭时的 minimal requirements 分层

- 调查结论：Core + MCP 运行时直接需要 `httpx`；`mcp`、`fastapi`、`uvicorn`、`websockets`、`psutil` 保持运行依赖；`pytest` 属于 dev/test，不应作为纯运行时 minimal 依赖；Memory ML 链保持 optional。
- 建议计划：补 `httpx>=0.28.0`；将 pytest/pytest-timeout 分入 dev/test；修正 setup/start 脚本的依赖探测；保持 QQ bot 与 Memory optional 分层。
- 状态：只读审计完成，尚未实现或合入。

## 六、下一步索引

1. 完成并验收 Codex 两个 Session 设置，确认默认不传、恢复默认、值校验和 idle/pending_restart/respawn 生命周期。
2. 完成并验收附件/New Session 目录输入，重点检查 Windows 路径边界、非法目录和创建确认。
3. 对 Codex quota cache 建立正确的 main integration worktree，审查 API/UI 与全局存储语义后再合入。
4. 对 MCP `model_list` 建立正确的主仓库 integration worktree，核对独立仓库基线后再合入。
5. 决定是否实施 minimal requirements 分层建议。
