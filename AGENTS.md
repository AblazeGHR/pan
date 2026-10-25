# Pan 开发约定（所有 Agent 必读）

本文件是仓库级协作契约，优先级高于个人习惯。详细的代码/测试约定见 `CODEBUDDY.md`；跨平台路线见
`docs/plans&overviews/CROSS_PLATFORM_PLAN_20261025.md`；当前在途工作见
`docs/plans&overviews/DEVELOPMENT_PLAN_CURRENT.md`。

## 通用

- 前端唯一实现是 React（`packages/web/src/`）。改完在 `packages/web/` 执行 `pnpm build`；`dist/` 是产物，禁止手改。
- 后端测试用 `python -m pytest tests/ -q`；从 Agent/后台 Job/已配置应用终端运行时优先用
  `python scripts/run_tests_isolated.py -- <命令>`。禁止复用真实 data、真实 profile、8767/8768。
- 不提交密钥、真实账号、真实用户数据；证据只保留脱敏统计、命令、SHA 与入口。
- 不得为了“让测试变绿”降低断言、吞错误或把 unknown/uncertain 当成成功。

## 跨平台（2026-10-25 起生效）

**结构**

- **单一 main**：不建立长期 `windows` / `posix` 分支。平台差异只允许存在于平台层；业务代码通过接口使用。
- 平台层位置：`packages/core/platform/`（能力探测、路径策略、进程所有权、服务管理、秘密存储）与各平台 backend。
  平台私有实现可以放在 `packages/core/platform/<os>/` 或明确的 `*_win.py` / `*_posix.py` 文件。
- 共享模块**禁止**在模块顶层导入 Windows-only 模块：`ctypes.wintypes`、`msvcrt`、`winreg`、`pywin32` 等；
  必须懒加载或经平台层。共享模块也不得假定 `/proc`、盘符、反斜杠、大小写不敏感或可执行位存在。

**能力**

- 每项平台相关功能必须有 capability（如 `terminal.native`、`rewind`、`durableDetach`、`service.lifecycle`）。
- 不支持的平台：API 明确返回“不支持”，UI 按 capability 隐藏/禁用，**不允许**导入期崩溃或半可用状态。
- 新增/修改平台相关功能，必须同步更新能力矩阵文档（`docs/plans&overviews/CROSS_PLATFORM_PLAN_20261025.md` 的矩阵或独立矩阵文件）。

**CI 门禁**

- 每次 PR 至少三平台（windows / ubuntu / macos）通过：依赖安装、`import packages.web.server`、测试收集、单元子集。
- ubuntu 负责真实服务/浏览器/MCP 的慢层；Windows 专属（ConPTY/Job Object）测试按平台显式 skip，不得静默跳过。
- 共享代码出现新的平台硬编码（win32 顶层导入、硬编码 `\\`、`/proc` 假设）视为回归，CI/审查必须拦下。

**完成定义（平台 PR）**

1. 三平台导入与收集绿；2. 契约测试（同一接口的 Windows/POSIX 实现过同一套行为测试）；
3. 能力矩阵与用户文档已更新；4. 失败路径显式（unsupported/error），不是静默降级。

## 并行与集成

- 并行任务使用独立 worktree + 分支；按**路径所有权**拆分工件，避免同时改同一文件。
- 候选只在自己的分支提交；MA 负责整合、组合验证与目标分支推进。合并前必须核对祖先、diff 范围、测试与证据。
- Git 策略按任务确定：用户指定优先；未指定时才用默认（当前默认快进）。策略不包含 push、发布、服务重启或 8767/8768 操作。
- 报告必须区分：源码、单测、构建、真实 HTTP/浏览器、远端 CI、真实数据迁移、部署与人工验收。
