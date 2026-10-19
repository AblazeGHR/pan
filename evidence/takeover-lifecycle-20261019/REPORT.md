# Takeover 发布前阻断修复：交 MA 验收

- 工作树：`D:/project/pan-worktrees/takeover-lifecycle-20261019`
- 分支：`fix/takeover-lifecycle-20261019`；基线：`ba51b72ec95a08e9c64fd289f189d46746033000`
- 本交付仅覆盖发布前 takeover 生命周期和原生会话准入；未实现 source-aware-auto-report。整合、快进和发布决定留给 MA。

## 根因与修改

1. React 调用 Session 控制入口。旧 `kill_session_worker`、`restart_or_start_worker` 用可供队列消费的活 Worker 查询寻找控制目标。真实 takeover 取消 Pan consumer 并停止 CLI，使持有外部 Job 的注册 Worker 不再满足该查询。Kill 返回 offline、前端提示 Kill sent；Restart 走 `_create_worker` → `_prepare_offline_recovery`，被持久化的 external/unknown owner 拒绝，未捕获的 RuntimeError 变为 HTTP500。
2. 控制查询现在寻找注册 owner，与队列继续使用的活 consumer 查询分开。Kill/Restart 都先强制退休外部 Job，等待 `ActiveProcesses=0`，原子保存完整 owner/Job 证明后才释放 handle/owner。Windows 所有支持 takeover 的 adapter 均使用 Job，不再只有 Codex 有完整后代所有权。终端自然退出后，Job 仍覆盖后代。
3. Job 停止验证有 8 秒响应上限（Job 内部通常最多验证 5 秒）。超时保留单个在途 verifier 和同一 Job handle；重试不能并发发起第二个 verifier/提前 close handle。失败显式返回错误、广播 error，并在 queue lock 下将未确认责任持久化为 Illegal Offline。原子保存失败时回滚 metadata，保留 Job/owner，错误明确说明 Illegal 持久化失败，不能声称已保存。重试成功的全 Job 证明可退休外部失败责任，但不会擅自解除其队列 incident 锁。
4. Session 并发 Restart 对已替换 generation 合并返回，不再次创建 writer；Kill/Restart 仍在同一 Session 生命周期锁下串行。旧 worker-id Kill 若输给另一次 generation 变更，返回身份变更错误，不再报告空成功。Pan runtime 未确认停止也不能进入 takeover 或启动替代 CLI。
5. UI 成功提示改为 Worker stopped。刷新/重连后仍暴露 held 或失败 external owner 的控制身份，不能因 consumer 消失而把 Kill 隐藏掉。
6. 旧准入只检查 `cli_session_id`。CBC/Claude 的 ID、Codex 的 thread/start 返回 ID 都不必然证明已有可 resume 的原生会话。新增统一只读准入，应用于两组 POST takeover 和两组 GET command；失败返回可理解错误，不隐式发模型消息。
7. 原生准入条件：CBC/Claude 要原生 session 文件及可解析原生历史；Codex 要 state DB 行、存在的真实 rollout、匹配 thread 的 session_meta，以及至少一个原生 user/assistant conversation item（仅 ID、空 thread、缺失 rollout、陈旧 history index 不够）；OpenCode 使用原生 SQLite session 存在事实；Kimi 使用对应 profile 的 session index、目录及非空有效 state。Kimi 交互恢复命令继承 Pan 使用的 `KIMI_CODE_HOME`，避免查对 profile 后却恢复到另一个 profile。Pan history 和 assistant 数量均不参与授权，因此已有真实原生会话、Pan assistant=0 的导入/恢复仍可准入。
8. 两个 React 入口共享 `useTakeoverReadiness`。按当前 Session/CLI id/historyTotal/updatedAt/workerStatus 变化读取独立只读接口，显示禁用原因，切换 Session 或事实变更期间禁用并拒绝迟到响应覆盖。轻量 session-summary 不增加原生磁盘扫描；后端执行入口仍独立重验。

## 红测和最终验证

- 首批生命周期红测：3 failed，分别证实 Session Kill 漏掉 held owner、Session Restart 抛 unknown-owner RuntimeError、unknown Kill 未报错/未记录 Illegal。随后四个准入入口红测：4 failed / 3 passed，日志见 [takeover-red.txt](../../artifacts/takeover-red.txt)。
- 定向 Python：**120 passed，0 skipped，exit 0**。涵盖两个 bug、worker-id/Session 入口、并发/重复及混合 Kill/Restart、单 verifier 超时重试、停止/原子保存/handle close 失败、迟到 EOF、失去注册 Worker 的 external owner、五 adapter 的原生事实准入、Codex 真临时 SQLite/rollout、原有合法/Illegal/429 与队列/Steer 回归。2 条既有 Starlette/httpx/anyio 弃用 warning。日志 [takeover-unit.txt](../../artifacts/takeover-unit.txt)，[JUnit](../../artifacts/takeover-unit.xml)。
- 真实隔离 HTTP/Windows 进程/Chromium：**5 passed，0 skipped，exit 0**。真实 POST Session takeover → Kill/Restart；终端存活/自行退出 × Kill/Restart；用测试 barrier 确保两个控制请求真正重叠。重复 Kill 均返回实际 offline，并发 Restart 仅观察到一个替代 provider init。证明同时检查 owner_retirements 的 jobId / ActiveProcesses=0 和旧真实后代 PID 消失。测试根 PID 的原始 FILETIME 写入日志；由 retained Job handle 确定后代归属。日志 [takeover-http.txt](../../artifacts/takeover-http.txt)，[JUnit](../../artifacts/takeover-http.xml)。
- 上述最终 HTTP 实例端口：56944、61636、61659、55032、55056。每例 teardown 核验自有 server 身份并记录 port released。
- 最终源码/build 后另跑 Chromium：**1 passed，4 deselected，0 skipped，exit 0**，端口 **58638**、Chromium **153.0.8010.12**。实测首次会话禁用解释 → 原生会话建立后无需刷新解禁 → 点击 takeover → 刷新仍可 Kill → 模拟 Job retirement timeout 显式错误和 Illegal 红点 → 重试 Kill 真正成功。日志 [takeover-browser.txt](../../artifacts/takeover-browser.txt)，[错误状态截图](kill-illegal.png)。
- 前端定向：**33 passed / 3 files，exit 0**（TopBar、workerStore、共享准入 hook，包含 Session 迟到响应隔离和无 assistant 计数解禁）。[日志](../../artifacts/takeover-frontend.txt)。
- `pnpm build`：**exit 0**，含 `tsc -b`、Vite 和 72 个预压缩产物校验；有既有 >500 kB chunk warning。[日志](../../artifacts/takeover-build.txt)。
- changed-file ESLint：**0 errors / 4 existing SettingsPopover hook warnings，exit 0**；`git diff --check` 通过。[日志](../../artifacts/takeover-eslint.txt)。
- 两个旧回归 fixture 改为表达真实 `returncode` 退休事实，保留原恢复顺序/no-spawn 等断言；旧失败状态断言从 held 改为用户新要求的 error。未放宽原 skip、时限或安全断言。新增真实 Job/FILETIME 集成用例明确仅 Windows 适用，本次 Windows 实跑 0 skipped。

复验命令（均预计少于 3 分钟）：

```powershell
python scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_takeover_lifecycle.py tests/test_codex_takeover.py tests/test_legal_offline.py tests/test_session_worker_controls.py tests/test_queue_restart_hardening.py tests/test_steer_restart_priority.py -q -o addopts=
python scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_takeover_lifecycle_http.py -q -s -o addopts=
pnpm --dir packages/web exec vitest run src/hooks/useTakeoverReadiness.test.ts src/components/layout/TopBar.test.tsx src/stores/workerStore.test.ts
pnpm --dir packages/web build
```

## Mock 边界、环境和未验证项

- HTTP、WebSocket、真实 Job、真实父/子进程、metadata 原子持久化、队列与 React 入口均运行真实代码。测试专用 probe 仅替换 provider 的 resume 存储读取与终端命令：fake CLI 写自己的临时 resume 文件，外部 takeover 脚本创建真实后代；不是实际 Codex/CBC/Claude/Kimi/OpenCode 原生 TUI 成功恢复的验收。停止失败是 probe 注入 TimeoutError；另有独立单元用真实滞留线程验证响应截止时间和单 verifier 重试。未消费任何远端模型额度。
- 未运行实际 provider CLI/TUI、非 Windows OS、全量本地 CI 或远端 CI；未宣称这些通过。失去 Job handle 且无法证明后代退休的 owner 继续 fail closed，明确错误；不会自动删 metadata、锁文件或清 unknown。
- 全部测试数据位于 pytest 临时根，动态端口排除 8767/8768；未访问、重启或停止这两个端口，未使用生产 data，未停止其他人的进程。隔离 harness 按 listener/PID/creation time/command root 清理；Job 的 whole-tree 证明覆盖 detached 后代。
- 原 canonical node_modules 缺 xterm 导致首次 build 失败。核对依赖声明和 pnpm-lock 一致后，仅移动本工作树的 junction，改连已存在的一致缓存 `D:/project/pan-dependency-cache/249D2FC3A1A0FD51/node_modules`；未安装、修改共享依赖或写穿 canonical。原只读链接保留在本树 ignored `artifacts/dependency-backup/node_modules`。最终 tsc/build 通过，不把环境 prepare 当验收。
- 本树提交后应为 clean；精确交付 hash 由最终回执和 `git rev-parse HEAD` 获取。未追 main，未合 main/practical，未 push/tag/release，未进行生产服务操作。

## MA 验收动作

1. 核对本分支 HEAD、clean、相对基线文件清单和报告的日志/JUnit；用上述命令按需复验。
2. 对照截图和 Chromium 测试确认空会话禁用、原生建立解禁、takeover 后刷新可 Kill、失败可见 Illegal、重试实际停止。mock 的 provider 原生 resume 边界单独记录。
3. 结合组合风险完成 MA 验收后，再按用户指定快进策略整合。本任务完成不解除发布暂停，不授权生产重启，也不把发布后 source-aware-auto-report 纳入本次提交。
