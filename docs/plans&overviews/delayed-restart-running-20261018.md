# Delayed restart running 调查与修复

任务分支：`fix/delayed-restart-running-20261018`，基线：`9a6e945f28b4a008a80e1f2f269a16aa569e8a7a`。

## 结论与复现

截图只有 tooltip，无法确定按钮是否 disabled、是否曾请求过。真实服务与 Chromium 都确认：普通 running，以及 running + 手动锁定队列消息，均可点击两个入口，POST 返回 HTTP 200 / pending。队列上锁不是本次验证发现的原因。

发现并复现了另一条导致顶栏持续禁用的路径：

1. A 正在执行，队列有一条锁定消息；点击 Delayed restart 得到 pending。
2. 切到 B，断开浏览器网络并关闭其真实 WebSocket；A 在断线期间完整结束任务并完成重启。
3. 在 A 的新进程启动另一个实际挂起任务，确认真实 GET Session 为 running；锁定消息仍留在队列。
4. 在 B 重连，然后切回 A。修复前顶栏仍显示 pending 并 disabled，但真实 GET delayed-restart 已是 completed。

第 1–2 步的简化场景已在修改产品代码前复现失败：独立端口 61188，服务 GET completed，Chromium `disabled: true`，tooltip 为 `Delayed restart pending: waiting for task completion`。加强后的第 3 步验证了 running + 锁定队列也能与旧 pending 投影并存。不能据此断言用户当时一定经过了断线/切换路径。

## 根因与最小改动

`useWebSocket` 的重连/权威刷新只对当前选中 Session 执行 `refreshDelayedRestart`；其他 Session 的旧 pending 可留在 workerStore。服务端 resync snapshot 没有 delayed-restart intent。顶栏只读缓存，没有在切回 Session 时刷新，所以 pending/restarting 的幂等禁用门禁会持续生效。

- `TopBar` 在选中 Session 改变时 GET 当前 intent，复用 workerStore 的 epoch/revision 防护。不做轮询，不通过解除 disabled 来重发请求。
- `SettingsPopover` 保持原本打开即刷新的行为，并让 pending/restarting 直接体现在按钮文本和 tooltip。原本禁用时仍显示通用说明，状态文字又在按钮下方，容易误认成尚未接受请求。
- API、workerStore、backend lifecycle 均不修改。现有有效 idle、terminal finishing、queue unit、generation/owner、native turn 与 spawn-lock 门禁继续生效。

## 验证

- owned verifytree：`D:/project/pan-worktrees/delayed-restart-running-verify-20261018`。独立 frozen install、独立 pnpm store；原任务树的 canonical node_modules junction 未写入、删除或替换。安装 Job `job_0ec86fc9c53e67e6d8368c6d` 收到终态 completed / exitCode 0 后核验日志。
- 前端完整 Vitest：156 files / 1859 tests passed。相关 3 files / 40 tests passed；新增覆盖切回 running Session 恢复 intent，以及 Settings pending/restarting 禁用、completed/failed/cancelled 可重试状态。
- `pnpm build`、TypeScript 与变更文件 ESLint 通过。ESLint 有 SettingsPopover 既有 4 项 hook dependency warnings；构建有既有动态 import / chunk-size 提示。
- Python 定向 78 passed：`test_steer_restart_priority.py`、`test_codex_session_conversion.py`、`test_session_adapter_validation.py`。新增失败后显式重试及取消回归，检查锁定项不变、running 请求不停止进程、重试不重复 spawn。它们是确定性单元验证，不是真实 provider 故障实验。
- 真 HTTP/WS/Chromium：3 passed / 0 skipped。驱动真实 Settings 与顶栏按钮，校验 HTTP 200/pending、pending 说明及禁用、重复请求 intent 相同、旧 PID/创建时间仍存活、terminal result 先于 replacement spawn、恰好一次 replacement、锁定项保持、解锁后新进程交接、durable sent_to_cli 回执；断线/切换场景在 replacement running 时恢复顶栏可用。
- 服务由 `tests/support/isolated_http_server.py` 启动，独立临时数据根、真实 FastAPI/uvicorn、真实 WS 与真实 provider 子进程；provider 是本地确定性 stream 协议实现，不调用 CBC/Codex 模型，不是 mock HTTP 服务。浏览器没有 route interception；WebSocket instrumentation 只记录并关闭真实 socket 以制造断线。
- 每用例动态分配 owned 端口并拒绝 8767/8768；复用服务 fixture 的 PID/创建时间/命令归属核验，使用真实 Worker kill endpoint 清理自有进程，检查捕获的 provider 身份消失、服务退出和端口释放。日志与 XML：任务树 `_e2e_tmp/delayed-restart/chromium.log`、`chromium.xml`。
- Python compileall 与 `git diff --check` 通过。Python 环境缺 pytest-timeout 插件，存在既有 unknown timeout config/mark warnings；未隐藏失败或新增 skip。

## 重跑

普通后端验证使用 `python scripts/run_tests_isolated.py -- python -m pytest tests/test_delayed_restart_locked_queue_http.py -q -o addopts=`。未配置浏览器时用例名称为 http，不宣称 UI 覆盖。

Chromium 验证先构建当前源码，然后设置 `PAN_TEST_DELAYED_BROWSER_SCRIPT` 为本树 `packages/web/e2e/delayed-restart-locked-real.mjs` 绝对路径，`PAN_TEST_WEB_DEPENDENCIES` 为 owned frozen-install 的 `node_modules` 绝对路径，再运行 `python -m pytest tests/test_delayed_restart_locked_queue_http.py -s -q -o addopts=`。本文件夹具自行分配动态端口，公共 conftest 在导入前清除实用 PAN 环境并隔离存储。

## 边界与交付

未重现原截图发生时的完整操作历史；未验证真实 Codex native TUI/provider、真实 provider 取消/崩溃、OS/文件系统故障、人工点击验收。完成报告与 native 安全边界有既有/定向单元覆盖，不能等同于所有生产环境故障已做真服务注入。初始修复未改产品后端；后续整合的 MCP 输入校验修复见下节，Worker 生命周期仍未修改。

交付在独立任务分支提交，在 owned verifytree 核对相同提交；不快进 main/practical，不 push/release，不重启或探针访问 8767/8768，不修改 constraints 文件。

## 与插件管理器整合后的组合验收

收到 MA 要求后，确认 main/practical 均为 `1887aef2650466b86dd80bbe2485a439899420be`。通过真实 merge 将最新 main 合入任务分支，merge commit 为 `4a7ad8d6654ab3f5d2e08d9b6e7c9210bceb8464`，双亲为原任务提交 `a72e0e15` 与 main `1887aef2`；无冲突，不使用 reset/cherry-pick，不移动 main/practical。

新增真实 HTTP/WS 组合场景：running 时插件配置先请求 restart、手动 Delayed restart 先请求、idle 且队列锁定时改插件、无 Worker 时保存插件配置后显式 start。检查插件配置持久化后复用同一 pending intent，重复物理工具列表更新不重复 restart，旧任务进程保持存活；终态及订阅完成报告落盘后恰好一次 replacement，锁定项保持，解锁后交给新进程。相同插件配置再次保存为 applied，不产生多余重启。Chromium 另检查插件配置触发的 pending 在 Settings 中可见、按钮保持幂等禁用且不发送重复 POST。

合并后前端全量 **157 files / 1863 passed**；build 验证 **68 个压缩资源**。首轮后端扩大回归为 176 passed / 2 failed，保留 `_e2e_tmp/delayed-restart/merged-backend-before-fixes.log` 与 XML：

- Codex 可选 context 设置测试只 mock 旧 MCP 描述符解析，未提供新插件目录所需 manifest。改用现有真实 manifest manager；保留原测试名及所有可选值/默认值断言。
- alias 解析先于原始 MCP 名称类型校验，使非字符串输入的原有 Invalid 错误变成 Unknown；旧调用方还会先对未哈希输入执行 set。恢复 alias 解析前的类型/空白校验，并让 `_apply_mcp_servers` 先解析验证再去重，避免 TypeError 和配置变更。保留原断言，新增 5 个整数/dict/list/空字符串/空白输入不改配置的用例。此修复不改变 plugin restart 或 Worker lifecycle。

修正后 Session/idle/Delayed restart/native 定向 **83 passed**，其中 adapter validation **42 passed**。最终扩大后端与完整真实 Chromium 组合复跑冻结在 `da7d164940b11337772b2447ab788c961c152275`，后台 Job `job_885219e9669915d84c7d93d9` 已收到 completed / exitCode 0 终态通知，并核对两个子命令均 exitCode 0、测试前后 HEAD 一致。

最终扩大后端 **183 passed / 0 failed / 0 skipped**，174.84 秒；真实 HTTP/WS + Chromium 组合 **7 passed / 0 failed / 0 skipped**，29.33 秒。七组 owned 服务端口为 49751、62917、58637、64375、53594、49933、49960，均有清理成功记录；测试在清理时核对 PID/创建时间归属、provider 身份消失、服务退出及端口释放。插件触发 pending 的 Chromium 记录为 `requests: 0`，证明显示等待状态时未下发重复 restart。

按 MA 最新要求，不将旧基线全量结果充作组合证据：在该冻结提交上另跑受影响的 SettingsPopover、TopBar、workerStore、PluginSelection **4 files / 43 passed**，插件设置/idle 优先级/adapter/native 转换定向 **94 passed**，并重新构建通过 **68 个压缩资源**。记录分别为 `_e2e_tmp/delayed-restart/latest-main-targeted-frontend.log`、`latest-main-targeted-backend.log` / XML、`latest-main-build.log`；最终 Job 的日志/XML 为 `merged-backend.*`、`merged-chromium.*`，冻结证明为 `merged-validation.json`。

最后只补充交付记录与证据摘要，不改产品及测试源码。再次确认 `git merge-base --is-ancestor main HEAD` 返回 0，main/practical 仍为 `1887aef2650466b86dd80bbe2485a439899420be`。可提交的结构化摘要见 [result.json](../../audit/delayed-restart-running-20261018/result.json)。警告仍为 pytest-timeout 缺失、第三方 Pydantic/WebSocket 弃用提示及既有构建/ESLint 提示；没有放宽断言或新增跳过。
