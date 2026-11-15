# 三功能整合：CBC 错误归因 / Interaction Units V3 / Chat Markdown 行号定位

TA 交付记录。对象是 `integrate/three-features-ta-20261115`，基线 `main` = `3ddefa1a9834e4bdcc1d376c6a8daf33d8922339`。
本文件只陈述本工作树实际做过的动作与证据；不构成 MA 验收结论，也不代表已 push、已移动 main/practical 或已上远端 CI。

## 1. 交付 SHA

| 项 | 值 |
|---|---|
| 工作树 | `D:/project/Pan/data/workdirs/three-features-ta-20261115` |
| 分支 | `integrate/three-features-ta-20261115`（仅此分支指向 HEAD） |
| 基线 | `3ddefa1a` = `main` |
| **组合候选 SHA（唯一）** | 分支尖端 `git rev-parse integrate/three-features-ta-20261115`；其中「三功能 + 本树证据」的提交是 `10840e7b`，本报告作为其后的文档提交追加 |
| 组合体规模 | `git diff --shortstat main..HEAD` → 52 files, +3486 / -114（不含本报告） |

提交序列：

| SHA | 内容 |
|---|---|
| `803b3645` | 行号定位（`541a0e92`，零冲突自动合并） |
| `b74557d4` | CBC 错误归因（`4f4ba3dc`→`8ddcce9f`→`83ae9dab`→`a6b09df2`→`cfb7ccd0`，零冲突） |
| `7bdb76db` | Interaction Units V3（`79071ee9`→`66276163`→`dbe03097`，3 处自动合并，已逐段复核） |
| `cfab6a50` | 修复候选自带的一处真实浏览器红测（`card-no-answer.e2e.mjs`，见 §4） |
| `10840e7b` | 本树真实 HTTP/Chromium 重新生成的 CBC 证据 |

## 2. 候选与 main 的祖先/冲突面核对

三个候选均**不是** main 的祖先，均为「候选基线 + 新提交」的旁支。

| 候选 | 链 | 候选基线 | 基线是否 main 祖先 | 基线到 main 的提交数 | 真实冲突文件 |
|---|---|---|---|---|---|
| CBC | 5 commits，tip `cfb7ccd0` | `fa656211` | 是 | 2（均为测试提交） | 0 |
| 行号定位 | 1 commit `541a0e92` | `0025c9d6` | 是 | 584 | 0（3 文件自动合并） |
| Interaction V3 | 3 commits，tip `dbe03097` | `99890a0c` | 是 | 145 | 0 冲突；3 处自动合并需人工复核：`docs/skills/pan/SKILL.md`、`packages/mcp/server.py`、`packages/jobs/final_service.py` |

`packages/interactions/{api,store,contracts,trusted,runtime}.py` 与 `schema.json` 在候选基线与 main 之间**逐字节相同**，因此 V3 主体按候选原样落地；`LockerView.tsx`、`lockerModel.ts`、`CardQuestions.tsx`、`TrustedChoice.tsx` 在 main 侧未改动，合并结果等于候选版本。

## 3. 冲突决策（main canonical 优先，取候选新增语义）

1. **证据二进制不机械搬运**。候选自带 `artifacts/history-terminal/http/{cbc-*.png, browser.json, runtime.json}` 与 3 张既有截图的改写。`tests/test_history_terminal_http.py` 本身会把证据写回 `artifacts/history-terminal/http`，因此整合提交只取产品源码/测试/文档，证据由本 SHA 自己的真实跑动重新生成（`10840e7b`）。理由：候选截图与其 run-scoped `runtime.json` 不能冒充整合 SHA 的浏览器证据。
2. **`docs/skills/pan/SKILL.md`**：保留 main 现文本逐字不动，把 V3 章节作为独立小节插入「## 0. 快速开始」之前，不改写 main 既有段落（Pan skill 主源规则不受影响）。
3. **`packages/mcp/server.py`**：main 的工具实现不动，只新增 `submission_id` 参数与 `capabilities` / `submission` 两个分支；`quote`/`urlencode` 已在 main 顶层导入，复核无误。
4. **`packages/jobs/final_service.py`**：保留 main 签名，仅把候选的可选 `effect_guard` 透传给 `packages.actions.invocation.invoke`。
5. **`packages/actions/{invocation,job_creation,legacy_templates}.py`、`packages/actions/session_invocation.py`、`packages/interactions/{units,action_binding,creation_binding}.py`**：main 与候选基线一致或无同名文件，按候选新增语义整体落地；新模块对 `packages.jobs.final_contracts`/`final_service`/`chains`、`packages.actions.command_execution` 的依赖全部是函数内惰性导入，main 侧这些模块未被重构，无兼容缺口。

## 4. 一处必须说明的测试契约变更（`cfab6a50`）

`packages/web/e2e/card-no-answer.e2e.mjs` 原来点击整卡 `Submit` 按钮制造「过期草稿提交 → 后端 409 → GUI 刷新为终态」。V3 把 V1/V2 卡投影进统一单元 UI 后，该视图只有 `提交此问题` / `提交全部未处理问题`，没有 `Submit`，于是真实 Chromium 等 30s 超时。

A/B 证据（同一台机、同一隔离运行器、各自独立工作树与端口）：

| 树 | 结果 |
|---|---|
| `main` `3ddefa1a` | PASS |
| 候选 `dbe03097`（detached 工作树） | **FAIL**（同一超时） |
| 本组合 + 修复 | PASS |
| 本组合未修复 | **FAIL** ×2（`job_07ef772dd94b6d1bf3b6ff2b.log`、`job_f01c7f2877173452a1a0492d.log`） |

结论：这是**候选自带缺口被整合继承**，不是合并引入。修复只把驱动控件换成批量单元提交按钮，并补断言「终态后两种单元提交按钮都不存在」；「后端必须 409」「终态无 textbox/radio」「submit/respond/reject 直连 HTTP 均 409」全部保留，没有弱化断言。终态 UI 无整卡提交控件是 V3 的既定产品语义（`card.status==='no_answer_needed'` → 只读），本 TA 未改产品代码。

## 5. 验证矩阵（严格区分层次）

| 层次 | 命令/入口 | 结果 |
|---|---|---|
| 仓库 hook | `core.hooksPath=scripts`，`scripts/pre-commit` 对暂存 `packages/web/src/` 跑 `npx tsc -b --noEmit` | 正常触发并通过（`b74557d4`、`7bdb76db` 输出 `pre-commit: checking React frontend... / React frontend OK`）。注意：`git cherry-pick` 不触发 pre-commit，故该钩子是在常规 commit 上验证的 |
| 前端类型 | `packages/web: npx tsc -b --noEmit` | exit 0 |
| 前端构建 | `packages/web: pnpm build`（Pan Job `job_cd5c824b61f2c932a16a9e1e`） | exit 0，`✓ built in 10.68s`，`Verified 94 compressed production assets` |
| 前端单测 | `node scripts/run-vitest.mjs run` 五个受影响文件 | 5 files / **63 tests passed** |
| 后端聚焦 | `run_tests_isolated.py -- pytest tests/test_terminal_report_diagnostic.py tests/test_runtime_issues.py tests/test_history_terminal_http.py tests/test_interaction_units.py tests/test_interaction_units_http.py tests/test_interaction_session_invoke.py tests/test_interaction_job_creation_binding.py tests/test_interaction_card_http.py`（Job `job_89abbd3ea8f219d9a3bc599d`） | **103 passed**，exit 0 |
| 真实隔离 HTTP/Chromium（CBC） | `tests/test_history_terminal_http.py`（owned 服务器、随机非 8767/8768 端口、临时 profile、生产 bundle、真实 Chromium） | 通过；证据 `artifacts/history-terminal/http/{browser.json,runtime.json,*.png}` 由本树生成 |
| 真实隔离 HTTP/Chromium（V3） | `test_interaction_units_http.py` / `test_interaction_card_http.py` 的 `units=True` 分支跑 `interaction-units.e2e.mjs` | 通过 |
| 真实浏览器（行号定位） | `packages/web: PAN_E2E_PYTHON=<py> node e2e/editor-relative-links-runner.mjs` | editor **11 项**、chat **15 项** 通过，cleanup verified（端口 63191） |
| Locker/Interaction 回归扫描 | 21 个 `test_interaction*`/`test_locker*`/`test_user_*` 文件（Job `job_f01c7f2877173452a1a0492d`） | 169 项，**168 passed / 1 failed**（仅上述 `card-no-answer`，已修复并单独复验通过） |
| 全量收集 | `python -m pytest tests/ --collect-only -q` | exit 0（无引入期导入错误） |
| 远端 CI | 未运行 | 见 §7 |

红证据保留位置（未删除、未改写）：
`D:/project/Pan/data/background_jobs/logs/job_07ef772dd94b6d1bf3b6ff2b.log`、
`D:/project/Pan/data/background_jobs/logs/job_f01c7f2877173452a1a0492d.log`（修复前 30s 超时原文）、
`D:/project/Pan/data/background_jobs/logs/job_89abbd3ea8f219d9a3bc599d.log`（修复后 103 全绿）。

组件测试自身的既有 skip 原样保留，未新增 skip/xfail。

## 6. 能否同一组合快进到 main，以及 practical 的结论

**main**：可以。`git merge-base --is-ancestor main HEAD` 为真，组合体是 `3ddefa1a` 的线性后代，`--ff-only` 即可。

**practical**：`c4ee090a` 是 `main` 的**严格祖先**（`git rev-list --count practical..main` = 560，反向 = 0；其 reflog 只记录到 integration 分支的 Fast-forward）。因此：

- **不存在「第二个 practical 基线 SHA」的合理形态**。任何以 `c4ee090a` 为父、只含三功能的提交都会让 practical 首次出现 main 没有的提交，破坏「practical 只做线性落后指针」的既有不变式，后续 practical 更新将从 FF 退化为 merge/reset。
- **同一 SHA 对 practical 也是合法快进**：`git merge-base --is-ancestor practical HEAD` 为真。代价是 practical 一次性前进 560+ 个 main 提交，这是 MA/用户的范围决定，不是 TA 能自行冻结的。
- **practical 基线上只回移这三个功能不可行**。在 `c4ee090a` 上实做 cherry-pick（独立 detached 工作树，已探针并清理）得到 6 处冲突，其中 4 处是 modify/delete：`packages/actions/invocation.py`、`packages/actions/job_creation.py`、`packages/actions/legacy_templates.py`、`packages/jobs/final_service.py` 在 `c4ee090a` **根本不存在**；`c4ee090a` 的 Action/Job 层还是 `packages/jobs/{action_api,action_runtime,unified,actions}.py` 旧架构。而 V3 的 creation binding / session invoke 正是跑在这套 canonical 架构上：`practical..main` 在 `packages/actions`+`packages/jobs` 上是 **132 个提交、65 个新文件、+24027/-1894 行**。回移等于移植架构，不是整合功能。行号定位与 CBC 单独回移可干净应用，但那是「部分组合」，同样会制造 practical 独有提交。

**需要 MA/用户决定的分支点**（TA 不代替决定）：

- A：`practical` FF 到分支尖端（git 合法，代价=接收 560+ 提交的行为变更）；
- B：暂不动 practical，仅 main 前进；
- C：另开专项把 canonical Action/Job 层回移 practical（跨 132 提交的架构移植，需独立立项与独立验收）。

**practical 独有 dirty 项不被覆盖的证明**（未在任何工作树 checkout/commit 它们）：

- `scripts/codex_auto_rotate.py`：`git diff practical..HEAD` 对该路径为空；`practical:…` 与 `HEAD:…` blob 完全相同（`identical blob = YES`）。FF 不会写该路径，`D:/project/Pan` 里未暂存的删除状态保留。
- `artifacts/codex-auto-rotation/`：在 `HEAD` 树中不存在（`git ls-tree -d HEAD artifacts/ | grep -c codex-auto-rotation` = 0），属未跟踪目录，FF 不会删除未跟踪文件。
- 本次整合 diff 只触及 `artifacts/history-terminal/http/**`，与上述两者均无交集。

## 7. 边界与未做事项

- 未 push，未创建 PR，未运行远端 GitHub Actions（本 SHA 的远端 CI 状态未知；上文通过项不得当作远端 CI 结论）。
- 未移动 `main`/`practical` 引用：交付时 `main=3ddefa1a`、`practical=c4ee090a` 与开工时一致；`integrate/three-features-ta-20261115` 是唯一指向 HEAD 的分支。
- 未接触真实 `data/`、真实用户 profile、真实端口 8767/8768；所有测试经 `scripts/run_tests_isolated.py`，端口由运行器分配，profile 为临时目录。
- 未修改 `main`/`practical` 的其它 worktree、其它分支与 `docs/skills/pan/` 之外的历史文档。
- 临时探针工作树（`.main-baseline`、`.candidate-probe`）与其构建产物已删除；`packages/web/test-results/` 为 gitignore 目录。
- Chromium 真实跑动用本机已安装 Playwright 浏览器，headless；未做人工目视验收。
- 本文件是 TA 证据与决策记录，MA 可据此独立复核；不代表验收通过。
