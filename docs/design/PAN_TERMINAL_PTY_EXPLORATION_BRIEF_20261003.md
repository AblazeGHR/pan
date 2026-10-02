# Pan Terminal / PTY 第一轮并行探索

业务任务：T-TERMINAL-PTY-20261003。负责人：本 Session 的 MA。

## 用户已决定的需求

1. 探索部分 Adapter 在 Pan 内运行时，是否能保留后端进程及运行中任务，切换到网页
   可操作的原生 TUI，再交还 Pan。难度和可行性决定首版范围，不预先承诺所有 Adapter。
2. 浏览器/面板生命周期不改变 Pan 或终端进程行为。隐藏或断开仅释放连接；显式关闭终端才终止。
3. 默认终端随 Pan 服务生死；显式 runtime detach 后保留原 PTY/进程和重连方式。
   应探索与现有 Pan Job / agent_background_start 共享运行层；Pan Job 与 Windows Job Object 分开。
4. Terminal 全局管理，可选关联 Workspace 或 Agent Session。
5. PR #6 的回滚功能确定要合并。先建立可覆盖交互与自动化回滚的公共核心，再适配 PR。
   本阶段不直接合入 main/practical，不推送、不重启实际服务。

## 工作树与边界

- 集成树：D:/project/pan-worktrees/pr6-terminal-20261002。
- 集成分支：audit/pr6-terminal-20261002；用户授权 MA 管理自己的派生工作树并在此整合。
- TA 由 MA 在 D:/project/pan-worktrees 创建独立树，不在集成树或 canonical checkout 开发。
- PR 源码只读树：D:/project/pan-worktrees/pr6-terminal-source-20261002，HEAD 387a43ec。
- 公共既有审查：docs/PR6_TERMINAL_REVIEW_20261002.md。
- TA 使用 CBC deepseek-v4.1-flash，effort high~max，派发前先核对实际设置。
- 工作流文件只由 MA 在 D:/project/Pan-main/.workflow 维护，不提交 TA 树内 .workflow。
- 8768 仅供 MA 的 Pan Session 编排。TA 探针不得操作既有 Session、Worker、CLI thread、
  用户文件或任何既有服务。真实运行必须用自己的新进程、临时数据根和 loopback 空闲端口。
- 不打印 secrets，不读取/改写认证内容；CLI 版本、help、已安装程序源码可读。
- 如需依赖，用独立临时环境，不改全局环境、canonical dependencies 或正式依赖锁文件。
- 新增探针/设计使用 apply_patch 编辑；记录实测、源码推断、未验证项，不能以 mock 充当真实 CLI 证据。
- 不把 resume 新进程算作保留原运行进程，不把临时 PTY 自动化算作网页 TUI。
- 允许在自己的 worktree 提交专属探针与报告。不得自行合并 PR、发布或整合其它分支。

## 四条并行工作

### CBC 无中断 TUI（max）

核对安装版 CLI、Pan CBC Adapter 的流式协议、交互模式和任何原生控制/attach 能力。
探索同一后端多客户端，或原生 TUI + 可靠结构化旁路。可运行有限探针；不接管真实会话。
专属目录 audit/terminal/cbc/；报告 docs/design/PAN_TERMINAL_CBC_FEASIBILITY_20261003.md。

### Codex 无中断 TUI（max）

核对安装版 app-server、原生 TUI/client 接口和 stdio/ws 连接约束，探索保留后端/turn 的
原生 TUI attach；查验当前版本与最新源码差异。只对自建测试后端操作。
专属目录 audit/terminal/codex/；报告 docs/design/PAN_TERMINAL_CODEX_FEASIBILITY_20261003.md。
涉及 OpenAI 产品能力时使用 openai-docs skill 并遵守其本地/官方证据要求。

### 生命周期、detach 与 Job（max）

检查 background_jobs/background_runner、服务 lifecycle 和 takeover Job Object。
设计并实现独立最小进程/PTY 探针，证明默认终止、显式 detach、同 PID 重连、整树清理，
分别验证正常退出与父进程崩溃。明确 crash 语义和 Windows Job 限制，不改生产模块。
专属目录 audit/terminal/lifecycle/；报告 docs/design/PAN_TERMINAL_LIFECYCLE_JOBS_20261003.md。

### 公共 PTY 契约与 PR #6 接入（high）

设计共享的 PTY backend / runtime ownership / terminal attachment / automation screen observer
边界，给出 PR _PtySession 的替换映射、EOF drain、背压、resize、退出和清理契约。
可实现独立的最小契约探针，不先修改 packages 下的正式模块；接口建议标明依赖另外三条的决定。
专属目录 audit/terminal/contract/；报告 docs/design/PAN_TERMINAL_PTY_CONTRACT_20261003.md。

## 每个 TA 的交付和测试责任

必须交付：结论、可复现脚本/准确命令、安装版本、关键文件/函数证据、运行输出摘要、
PID/创建时间等身份核验、清理结果、失败和未验证项、接口影响及下一步建议。
报告中的步骤由 TA 自己完整执行；不能只写计划。不能运行时明确原因、已做替代检查和残余风险。
测试以针对性 CLI/PTY/进程行为验证为主，不要求无关全套。避免镜像实现的空测试。
完成后 git diff --check，提交专属文件，报告完整 commit hash、文件清单和测试结果。
需要扩展用户语义或影响受保护服务时报告决策门，继续无依赖的安全探索。

## MA 验收与下一阶段

MA 对照证据确认：是否保留原 backend、运行中任务、输入控制权及结构化事件；
detach 是否真的保留原进程；公共接口是否同时满足交互和 PR 回滚。
接受的报告/探针整合到当前集成分支，再形成实施方案并派发公共核心工作。
不会把 TA done 当作验收，不会因为原生 TUI 探索失败而无限阻塞普通终端和回滚接入。
