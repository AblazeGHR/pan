# 替身交接 originalPrompt 可选覆写：TA 交付证据

工作树：`D:/project/pan-worktrees/handoff-original-prompt-20261020`；分支：`feature/handoff-original-prompt-20261020`；基线：`fc0c8d6f836509ced4784229b12aacda8dfbc4b5`。本交付仅提交当前分支，等待 MA 验收，不执行整合、快进、push 或发布。

## 实现与契约

- Core 新增 `override_original_prompt=False`、`original_prompt=None`。HTTP 对应 `overrideOriginalPrompt`、`originalPrompt`，MCP 使用 snake_case。关闭或省略开关时完整继承 A.original_prompt，残留文本无效；启用时必须提供字符串，空串明确清空，文本不 trim。选择与 copy_settings 独立。
- Core/HTTP 在创建后继和迁移关系前校验新增参数；MCP 在可能认领 Session 的 access 检查前校验，并使用 StrictBool/StrictStr 防止 MCP 调度层强制转换错误类型。
- B.handoff_prompt 仍是本次必填简报，B.system_prompt 计算为本次简报 + 选定 original_prompt。A 两个 prompt 不修改，不继承旧简报。不改关系、订阅、Job retarget、owner 或新 CLI 身份的既有交接策略。
- 当前基线没有 React handoff 弹窗，因此补齐 Sidebar Session 菜单入口、API 封装和复用 Modal 的表单。开关默认关闭，textarea 受控，启用且留空明确清空。失败保留输入；关闭卸载、按 Session ID 隔离，重开或切 Session 重置；同 Session 数据刷新保留草稿；关闭后迟到的成功/失败不关闭新弹窗。提交成功后刷新列表失败不会变成可重复提交的失败表单。
- demo 明确返回不支持交接，避免伪造关系迁移成功。同步中英文用户手册、HTTP API 和 `docs/skills/pan/SKILL.md` 主源。

## 验证

所有命令均在本工作树执行。Python 使用 `D:/project/Pan-main/.venv/Scripts/python.exe`。前端 canonical node_modules 缺 xterm，当前树创建本地 overlay，逐项只读复用 canonical 依赖及现有 `D:/project/Pan/packages/web/node_modules/@xterm`；没有安装包或修改共享依赖、package.json、锁文件。

| 层级 | 命令 / 范围 | 结果 |
|---|---|---|
| 后端定向回归 | `python -m pytest tests/test_handoff_original_prompt.py tests/test_session_handoff.py tests/test_session_prompts.py tests/test_handoff_job_retarget.py tests/test_session_adapter_validation.py -q -ra --junitxml=.validation/handoff-original-prompt/pytest.xml` | 134 passed，0 failed/error/skipped，exit 0 |
| React/jsdom | 在 packages/web 执行 `pnpm exec vitest run src/components/session/HandoffSessionModal.test.tsx src/components/session/SessionMenu.test.tsx src/demo/mockBackend.test.ts` | 3 文件、40 passed，0 skipped，exit 0 |
| 静态类型 | `pnpm exec tsc -b`，以及最终 build 内的 tsc | exit 0 |
| 改动文件 lint | `pnpm exec eslint` 检查新增 Modal、其测试、SessionMenu 及测试、Sidebar、api、demo 及测试、E2E mjs | exit 0 |
| 仓库 lint | `pnpm lint` | exit 1：140 errors、19 warnings，位于本次未改文件；未扩展修复，未单独执行基线 lint |
| 构建 | `pnpm build` | exit 0；Vite production build 与 72 份预压缩资源校验通过；保留已有 chunk 大小/动态静态混用提示 |
| 真实 HTTP + Chromium | `node e2e/handoff-original-prompt.e2e.mjs` | 自有 OS 动态端口实例：8 组设置/覆写组合、16 个无效请求；生产 Sidebar → 菜单 → 弹窗，默认关闭、关闭重开、A/B/A 重置、空串覆写实际落盘；0 page errors，exit 0 |

新增后端用例覆盖省略开关（包括携带残留文本）、false、true 新文本、true 空串、错误开关类型、启用但缺少值/null/非字符串、两种 copy_settings、源 prompt 不变、computed prompt 精确文本、持久化冷读、管理/报告关系、新 CLI/owner/Steer 回执隔离；MCP 既检查 HTTP 字段映射，也实际进入 FastMCP 调度验证 Strict 类型拒绝。原 Job retarget 与 adapter 回归一并通过。

本地证据保存在当前树被忽略的 `.validation/handoff-original-prompt/`：`pytest.log`、`pytest.xml`、`vitest.log`、`vitest.xml`、`lint-changed.log`、`build.log`、`results.json`、`server.log`、`override-empty.png`；E2E 每次使用新的 runtime 子目录，不清理或覆盖既有 Session 数据。该目录是本地验证产物，不纳入提交。

## 边界

- 真实实例使用真实 FastAPI 路由与独立磁盘存储，关闭 lifespan；没有启动真实 CLI Worker，不证明 provider 实际消费 prompt、活跃 Worker 并发交接或 OS 行为。
- Job retarget/owner/订阅相关行为有 Python 回归证据，浏览器链路没有启动真实后台 OS Job。失败保留和迟到响应由 jsdom 定向测试验证。
- 未做全量测试、远程 CI、移动端真人验收或 MA 验收；未操作 8767/8768、既有服务、main/practical。自有 E2E 进程在 finally 中结束。
- 每个验证命令预计及实际均低于 3 分钟，无需启动长时持久 Job；未轮询现有服务或后台 Job。
