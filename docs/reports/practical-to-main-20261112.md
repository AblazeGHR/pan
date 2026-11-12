# practical → main 组合候选（2026-11-12）

## 范围与祖先

仅在 `data/workdirs/practical-to-main-20261112`、分支 `integrate/practical-to-main-20261112` 工作。
冻结 main `f6a0c7f38a3016b608c72415ad40055e0a83b03b`、practical `c4ee090acafd245ae49f49b30a6d094e68d46b06`。
保留 MA 发起的 merge，未 reset/abort。两父 merge 为 `70a8044ff64baa3bf9cd2619df9650bd4d636735`，父提交依次就是上述两个 SHA。
随后依授权 cherry-pick 目录按钮候选 `b6de824eecaf516a550e109940df9cf70845e79e`、`0ffe17508740b9ae44318bebbc09a27fc20b05b6`，本树对应 `f960619a`、`78e0291f0d9985ff01f304eee8d03f2edf6df5fe`。
本报告随最终 rev3 修复提交；最终精确组合 SHA 见交付回执（不在文件内写自引用 SHA）。

没有移动 main/practical refs、push、部署、重启既有服务或写源目录按钮 TA 树。

## 功能矩阵

逐项审查 `main..practical` 的 21 个提交与功能路径，包含自动合并文件。初次整合路径清单保存在 `.tmp/practical-main/source-inventory.json`，该清单说明当时相对 main 是否已有同等实现，不代表 rev3 之后文件仍逐字相同。

| practical 提交 / 功能 | 组合处理 | 验证及语义边界 |
| --- | --- | --- |
| 0ed77055、0642c05c Settings Search 独立标签、窄屏无痕滑轨 | 保留独立 Search tab、触摸/键盘横向滚动；rev3 用共享设置描述自动生成字段索引 | 全 Vitest；production HTTP Search → Behavior 实际开关；320/360/390/1600 CSS/Chromium 滚动测试 |
| rev3 字段检索根因 | 分组/字段标签、hint、别名共用 `settingsDescriptions.ts`，UI 直接读取；稳定 `data-settings-id`；重复标签使用不同 scope ID | 新字段模拟自动入索引、label+hint 跨词搜索、实际 UI → 索引及索引 → UI、漏注册具名 AST 检查；无挂载隐藏面板搜 DOM |
| rev3 可见性 | 权限、平台能力、Tunnel 由相同描述/可见性规则判断；插件配置来自原面板请求的已知事实，Search 不额外请求；动态快捷键来自现有注册表，插件消息来源来自原来源目录 | 未登录/平台 denied/配置隐藏项测试。插件细项在已有面板读取配置前保持未知而不宣传可用；组入口仍可搜索。运行状态、错误结果、临时确认文字不作为设置字段 |
| 4777a5e2 Select 拖选、批量 Manage/msgBridge、过滤菜单 | main 已包含大部分同等实现；保留 main 管理边界、确认和错误回滚，只修 E2E 的 maintenance 健康夹具 | production Chromium 真 HTTP 拖选三 Session，确认前无 PATCH；批量 canClaimUnmanaged 与 msgBridgeEnabled 持久化；桌面/窄屏选择 E2E |
| 5efe578f、fc799146、c4ee090a Behavior | 合入全局与 Session override，revision CAS、成功后更新、广播；文案明确已有与新队列 | Python / HTTP / Chromium；影响 auto_lock_queue 原策略入口，关闭不会清理已有锁，不越过手动锁、Illegal、unknown/执行安全门 |
| d100fbef 隐藏启动前可见回退 | 将 practical 的前置窗口能力失败接入 main Windows ProcessOwner；不恢复旧 writer | 仅 typed HiddenWindowUnavailable、尚未 Popen、资源 cleanup 确认后一次 visible；已进入进程创建、已有进程或 unknown 不 replay；原 effect/Attempt/receipt/owner fence 保留 |
| 1430c99a 原始消息、编辑器 raw/download | main 已有原始 rawContent 路径及不可预览文件下载路径，保留 | 全前端与 attachment protocol；没有重构 raw 正文、放宽内容体积或把 binary 伪作可编辑空文件 |
| 545d7067、40be1a24、41f86101 editor reveal、运行能力、路径范围 | 保留 main 平台层与实时 fileManager 能力判断 | editor reveal / attachment API 回归；未操作用户 Explorer 或真实文件 |
| aed15965 通知设置自动保存 | main 已有；保留即时保存、失败不冒充成功、已提交 store 真值 | SourceNotification/Completion/ErrorExclusions 单测；通知真实 HTTP 与 production UI |
| 86e42155 通知错误覆盖 | 保留 main 更完整错误/供应商诊断覆盖；不退回旧 Save 按钮假设 | test_notification_kind_http 与既有前端覆盖；夹具不改产品通知门 |
| 6986fdf3 CC Switch duration 天数 | main 已有同等格式化实现 | 全 Vitest；本轮未独立重跑真实外部 CC Switch |
| 51db605c Session creating 读取保护 | main 已有临时 identity 保护，保留全部关联读取屏障 | sessionStore.creating/race、hooks/store 完整 Vitest；目录选择不创建临时 Session |
| 0adb4c3d Mod+End | main 已有 chatScrollBridge/shortcut registry，保留上下文和焦点行为 | registry/chatScrollBridge/useGlobalShortcuts 完整 Vitest；无另起快捷键引擎 |
| b930a902、8bfca671 Remote 页面与报告 | main 已有 Remote 独立分组/折叠/草稿状态；rev3 只共享描述和锚点 | RemoteSettingsSections 单测、Search 定位；未改真实 Tunnel/LAN/Tailscale 配置或端口 |
| 38fcf67c 自动调度设计、98cda171 Scepter spec | 保留文档，不将设计稿当已实现引擎 | 源码/文档审查；无新调度器、旧 writer 或 dispatch 自动执行 |
| 55df96a4 忽略规则 | 保留依赖/证据忽略规则；新增本次精确隔离路径 | 证据位于忽略目录，不提交 profile/密钥/真实数据 |
| b6de824e、0ffe1750 New Session 原生目录按钮 | 原提交顺序 cherry-pick；directories-only 无 Session 入口，files/未知 400 且不调用 helper；附件原 sess.get 门保留 | 本树 16 picker backend 测试、平台测试、目录/Modal 前端、production Chromium 接线；无临时 Session |
| 目录生命周期组合修复 | workdir 值变化在 layout effect 使旧请求失效；用户编辑、关闭/重开、取消不会被晚到结果覆盖 | 原 Modal/DirectoryInput 断言保留，取消用例等待真实 prefill 就绪；production selected/cancel/unavailable/busy |

## 六处冲突决策

| 冲突路径 | 决策 |
| --- | --- |
| `docs/plans&overviews/CROSS_PLATFORM_PLAN_20261025.md` | 合并双方能力条目，保留 main POSIX、时区、retention、bridge 条目及 practical 回退；目录候选再增 fileDialog.native，不删除平台边界 |
| `packages/jobs/desktop.py` | 保留 main 兼容导入 shim，实际 Windows 改动放 `core/platform/desktop_win.py`，不整文件覆盖旧实现 |
| `packages/jobs/independent_runner.py` | 保留 main 懒加载 Windows 导入/生命周期，增加特定前置失败审计；不恢复旧 Store 权限 |
| `packages/web/e2e/notification-kind-coverage.mjs` | 保留 main 更完整诊断与即时保存契约；不恢复旧 Save 按钮 |
| `tests/test_notification_kind_http.py` | 保留 main 最新诊断/通知覆盖 |
| `tests/test_platform_layer.py` | 保留 main POSIX ProcessOwner、job.runtimeOwner、time.hostZone 及 fileManager 动态探测；移除 merge 引入的重复探测定义，未移除断言 |

## main 安全契约

canonical-only `FinalService` / `FinalRuntime`、Action/Group、owner fences、Attempt、receipt、retention、单 Jobs 入口保持。旧 JobStore 两个 practical 测试被改为真实 canonical ExecutionStore / execute_claimed：必须证明 marker 仅一行、同 receipt 记录 effectiveHideWindow/失败原因、cleanup confirmed、取消杀完整自有树。没有重新启用旧 writer 来满足旧测试。

Windows 回退只能发生在 `OwnedPopen` 进入原生创建之前。desktop/guard 失败必须是特定异常且 cleanup 全部成功；cleanup 失败直接失败。已存在进程、进入 native 创建阶段、身份 unknown 均不重放。执行原 receipt 增加窗口审计事实，原日志记录原因；没有第二套执行或收据协议。

## 验证记录

所有长测/构建/提交均使用当前 practical 服务提供的直接 `mcp__pan__agent_background_start`，按原 terminal notice 核验。实际运行服务仍是 practical 的 Job；候选 canonical 路径在隔离测试中验证，二者未混淆。

- 依赖正常 `pnpm install --frozen-lockfile`：Job `job_47981f3bc3f48ed02ac88c28` exit 0。
- 初始 focused backend：246 cases / 3 failure / 19 既有平台 skip，Job `job_ec2b3ffb7e48fbff3e8f003a` exit 1。两项为退役旧 Store 的旧断言，改成 canonical 测试；一项为 Windows lifetime guard 线程时序失败，原断言不变，修后精确 15 cases / 0 fail / 0 skip，Job `job_b8ee0313532cfb47892725a5` exit 0。
- 初始真 HTTP / production Chromium 组合：82 cases / 0 fail / 0 skip，Job `job_90b850104b97c984c165ae9f` exit 0。
- 原 merge 全前端：2829 pass / 3 既有 skip；build 94 assets，Select desktop+narrow E2E 通过，Job `job_fec7fa754672160d1144e86f` exit 0。
- 原 merge 正常 hook / commit：Job `job_861175ec513c3d15092f083e` exit 0。早先错误 Git 路径 Job 未执行任何命令，未伪称成功。
- 加入目录按钮后 backend：154 cases / 0 fail / 0 skip（含全部 16 native picker、9 platform API、18 platform layer、13 Behavior、12 fallback、24 retirement、62 independent execution），Job `job_cfe2ee0f2201b678bc19f448` exit 0。
- rev3 红证据：metadata-round2/3、forward-coverage、forward2；未吞退出码，逐项补字段锚点/可见性。第一次完整组合前端 2850 pass / 3 既有 skip，但后续 build 暴露 GROUP_OPTIONS 的 labelId 类型漏补，Job exit 2；下一次全套出现两项异步就绪失败，保留 XML 与修复原因。
- 最终组合前端：248 files、2850 pass / 0 fail / 3 既有 skip；`pnpm build` exit 0，94 compressed assets；320/360/390/1600 独立 Search/无痕滑轨 Chromium 检查 exit 0。Job `job_f52943020cdf74d56fce9a2a` exit 0；证据 `combined-vitest3.xml`、`combined-build4.log`、`combined-search-layout.log`。
- 最终组合隔离 backend / production HTTP：Job `job_7abc315312ab7481bf5cbecb` completed / exit 0：355 cases，336 passed、0 failure/error、19 既有平台 skip；`combined-backend.xml`。包括真实 HTTP/production Chromium Search/Behavior/Select、目录按钮接线及 canonical 回退/cleanup 验证。

日志/JUnit/浏览器 JSON 位于本树 `.tmp/practical-main/`；Pan 持久 Job 日志由各原 jobId 指向。旧 tracked 证据的本轮生成输出另存 `.tmp/practical-main/generated-evidence/` 后恢复原 blob，不覆盖历史证据。

## 验收边界

本机 Windows/Python 3.14、本树依赖、隔离 HTTP 与真实 Chromium；生产 React bundle 未用 jsdom 代替。目录浏览器用例只脚本化 OS picker 的返回，route/gate/React/取消/错误/零临时 Session 均真实；真实 Windows 目录/文件 OS 对话框证据由原目录 TA 提供并经 MA 审查，本轮不冒充重做。

没有验证远端三平台 CI、fresh Ubuntu 浏览器、真实 data/profile 迁移、真实用户服务重启/部署或人工 UX 验收。最终 exact-SHA 远端 CI 与推进 main 由 MA 独立完成。

## 关机前停止点

用户要求当前 Job 完成后无论结果如何停止。本轮 Job completed / exit 0，已核验 JUnit；没有启动后续测试、修复或提交。
当前 HEAD `78e0291f0d9985ff01f304eee8d03f2edf6df5fe`，rev3 共享检索描述与目录组合生命周期修复仍在工作区，尚未提交/正常 hook，不能当作最终组合 SHA。
main/practical refs 仍是冻结值。后续恢复应保留本树全部改动与本轮证据，先审查工作区，再正常 hook 提交与交付精确 SHA；不要重置或重做原两父 merge。
本轮通知测试与 Windows proof 更新了历史 tracked 证据文件，尚未再次归档/恢复，恢复工作时应先另存本轮生成输出并核对旧 blob，不混入源码候选提交。

## 恢复与最终审查

收到用户“继续”后恢复原树。已核验分支与三个 refs 未漂移；最终 9 个测试生成文件归档在 `.tmp/practical-main/generated-evidence/final-combination/`，包含 SHA256 manifest，随后恢复原 HEAD blob。
审查补上共享锚点关键词排除规则：`searchable: false` 的运行状态文字不能作为可见设置项的补充关键词；不同权限 gate/capability 的描述也不交叉传播。回归测试同时检查这两类隐藏文字。
最终补充前端/浏览器 Job `job_a4aee8f9778499ad57589485` completed / exit 0：完整 Vitest 248 files、2851 passed / 0 fail / 3 既有 skip；production build 94 compressed assets；两项真实 HTTP/production Chromium 2 passed / 0 fail / 0 skip。证据 `delivery-vitest.xml`、`delivery-build.log`、`delivery-browser.xml`；交付源码/测试 SHA256 manifest 为 `.tmp/practical-main/delivery-source-sha256.json`。最终检索过滤改动未影响后端实现，后端完整 355 cases 证据仍保留。

## 可复核命令

工作目录 `packages/web`：

```powershell
pnpm install --frozen-lockfile
pnpm exec vitest run --reporter=default --reporter=junit --outputFile.junit=D:/project/Pan/data/workdirs/practical-to-main-20261112/.tmp/practical-main/delivery-vitest.xml
pnpm build
node e2e/settings-search-tab.e2e.mjs
```

工作目录仓库根；每条命令按真实退出码判断，不通过管道隐藏失败：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_native_file_picker.py tests/test_platform_api.py tests/test_platform_layer.py tests/test_behavior_override.py tests/test_job_window_fallback.py tests/test_canonical_only_retirement.py tests/test_job_final_independent_execution.py tests/test_platform_process_contract.py tests/test_platform_process_windows_hidden.py tests/test_job_global_retention.py tests/test_job_retention_process_logs.py tests/test_behavior_override_http.py tests/test_practical_merge_http.py tests/test_practical_native_directory_http.py tests/test_notification_kind_http.py tests/test_editor_reveal_external.py tests/test_attachment_parts_protocol.py tests/test_job_final_http_service.py -q --junitxml=.tmp/practical-main/combined-backend.xml
python scripts/run_tests_isolated.py -- python -m pytest tests/test_practical_merge_http.py tests/test_practical_native_directory_http.py -q --junitxml=.tmp/practical-main/delivery-browser.xml
```

实际 Jobs 使用 provisioned `C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe`；正常 hook 执行 `scripts/pre-commit` 中的 `npx tsc -b --noEmit`，没有跳过 hook。

最终提交前再次核验：已测源码/测试与 SHA256 manifest 全部一致；main/practical refs 未变化；两父 merge 和两次目录 cherry-pick 保留。报告中的关机停止点为历史记录，用户“继续”后已恢复交付。
