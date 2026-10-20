# T-COMBINED-queue-force-multiplier-20261021/1 整合候选

本报告对应隔离分支 `integration/combined-queue-force-multiplier-20261021`，工作树
`D:/project/pan-worktrees/combined-queue-force-multiplier-integration-20261021`。
基线 `b8078d345712fdbd3460f9c52b03271ed8d75d6d`。承接已存在的隔离树与未完成 merge，未丢弃前任改动。

## 整合来源与冲突处理

- Illegal/Wake `d062b99714447f455a8976015ac371639f82d4c5`，已合入 merge `dfa335b2`。
- CBC 倍率 `1bb38147f34c75c0f33cf18df683a3428e2c4f36`，已合入 merge `163c1383`；验收证据 `a3bd4053c5b7173a114032dad396706d6adbce46`，merge `24b0564b`。
- 自动 Force `14b17ed1c99fd7549c9a4c9986c56b0ac60d4739`，本次完成其 merge。
- `worker.py` 三处文本冲突：`_persist_task_item` 同时保留 `wake` 与 `auto_force`；显式 Force 的 Illegal 分支只持久入队；非 Steer running 分支保留原 queue ID/置首/FIFO，并用 `allow_illegal_recovery=False` 防止隐式恢复。竞态返回 `illegal_queue_hold` 时仍回 queued 和原 ID。
- Wake 显式关闭自动 Force，避免带 MA 来源的 Wake 在非 Illegal 场景被自动 Steer/restart。
- 自动 Force 在队列快照广播后重新准备/检查 Illegal、当前 Worker、Pause、readonly、eligibility 与 edit lease，再允许 restart。
- 设置和前端非冲突合并经复核：Wake 文案与三项默认 off 的独立自动 Force 开关同时保留；CBC 倍率仅展示，组件/API model 仍提交纯 ID。
- 当前 Pan skill 的“补充当前任务默认 Force，明确独立后续才普通 send”原文保留；新增 Illegal 限制不覆盖其用途规则。Action Group 冻结文档与基线内容完全一致。

## 本轮验证

所有 Python 测试经 `scripts/run_tests_isolated.py` 清除继承 PAN_*、使用隔离数据。执行环境为 canonical Python 3.14.5；前端通过 gitignored 本树 node_modules junction 复用 canonical 依赖，未更改共享依赖。未运行全量测试或收费模型。

| 批次 | passed | skipped | failed | 日志 / JUnit |
|---|---:|---:|---:|---|
| Illegal/Wake、自动 Force、CBC 模型/API 元信息核心 | 136 | 0 | 0 | core-final.txt / core-final.xml |
| 队列 API、Illegal HTTP/Wake ready、native、handoff、shortcut、retention、设置与寻址兼容 | 485 | 3 | 0 | compat.txt / compat.xml |
| 补充 Worker primitives、CBC Steer、queue migration、task ID、manual report | 132 | 1 | 0 | queue-native.txt / queue-native.xml |
| 独立 HTTP/WS、Chromium 自动 Force 设置、真实队列 fixture | 10 | 0 | 0 | http-browser-final.txt / http-browser-final.xml |
| **Python 合计（不同测试身份，不计重复重跑）** | **763** | **4** | **0** | summary.json |
| 前端 12 个定向文件 | 304 | 3 | 0 | frontend.txt / frontend.json |

前端测试覆盖 ModelSelect/modelLabel、NewSession/Import/SettingsPopover/SessionItem、Wake 入口、queueStore、三开关存储及保存失败。CBC Python 覆盖当前 JWT sub 精确账号选择、其他账号排除、缺失/损坏数据、0/非法倍率、TTL 与 reload 失效；前端断言倍率显示/搜索及 onChange/API 纯 ID。
自动 Force 覆盖三开关独立性、idle/running/non-Steer、unknown/rejected 不自动重投、原消息身份、附件与 gate；新增三项组合回归覆盖 manager Wake、显式 Force Illegal 竞态和自动 Force 快照 Illegal 竞态。

- `pnpm exec tsc -b`：exit 0，见 typecheck.txt。
- `pnpm build`：exit 0；校验 **72 compressed production assets**，见 build.txt。存在既有 chunk >500kB 提示。
- 改动前端文件 scoped ESLint：exit 0，0 errors / 7 warnings，见 lint-scoped.txt。
- `pnpm lint`：**exit 1，140 errors / 19 warnings**，见 lint.txt。140 个错误位于 17 个 E2E 脚本；其 Git blob 全部与 b8078d34 相同（summary.json 列出逐文件对比），未为通过而关闭规则或修改无关脚本。
- 初轮核心出现 3 个失败，见 core.txt/core.xml：两处旧 Illegal 测试夹具缺 owner/operation 字段导致 ordered writer 拒绝，改用真实 admit/detect；一处 restart mock 断言同步新增 `allow_illegal_recovery=False`。未削弱产品断言或新增 skip。
- 测试期间观察到 Pydantic/FastAPI/AnyIO deprecation 与已有 handoff 测试未 await broadcast 的 RuntimeWarning，保留原日志，未宣称无 warning。

精确 Python argv 与退出码见 commands.json。前端在 `packages/web` 执行：`pnpm exec vitest run` 加 frontend.json 内 12 个 testResults 路径；`pnpm exec tsc -b`；`pnpm lint`；`pnpm build`。定向 lint 对 `git diff --name-only b8078d34` 中所有 packages/web 的 ts/tsx/mjs 文件执行 eslint。

## Skip 审计

没有新增 skip。四个 Python skip 均来自候选中已有的 retired Oneshot Worker 测试，具体身份与原因见 summary.json：CBC capability、普通 oneshot send、force oneshot restart、当前输入 taskId report。前端三个既有 skip 分别在 quickNewSession、InputRow CBC stream/native/oneshot capability、NewSession output-mode/template 夹具；精确测试名同见 summary.json。Job one-shot 调度没有被移除。

## 安全边界与证据分层

- main 仍为 b8078d34，practical 仍为 20473b41；未移动两引用，未 push/release，未重启既有服务。
- 8765/8767/8768 listener 前后快照见 listeners-before.json / listeners-after.json，内容一致。测试仅用动态分配端口。
- HTTP/WS 与 Chromium 本轮重跑，使用确定性 fake_stream_cli 和 Steer receipt fixture，证明 Pan 路由/身份/设置/浏览器行为，**不证明真实供应商模型消费或真实 native provider admission**。
- CBC `a3bd4053` 的真实账号 HTTP/浏览器验收是继承证据，本轮未重新读取真实账号或发起模型请求；本轮使用 synthetic account/cache 单测及组件提交断言。倍率缓存保持候选的 TTL 语义；账号外部切换在 TTL 内可能仍显示旧缓存，需 config reload 或等待过期，未新增账号切换即时失效机制。
- 未验证真实 QQ/WeChat、独立 Job 服务、远端 CI、真实用户数据、实际服务上线。native/handoff/shortcut/retention 为定向本地回归，不等于端到端真实供应商测试。
- 本轮还发现自动 Force HTTP fixture 停止函数只捕获启动期后代，漏掉后续 fake CLI；已补停止前 PID/create-time 后代快照并重跑。先前两次运行合计 16 个遗留 fixture Python 进程已按脚本路径、测试 cwd、PID/create-time 逐一核对清理，见 fixture-cleanup.json。最终残留检查另存 owned-runtime-leftovers.json。

候选可供 MA 审查；全目录 lint 的既有失败和以上未验证边界仍应保留在后续验收决策中。

归档 txt/xml 日志仅清理尾随空白，测试内容与计数未改；提交前 React 检查通过。
