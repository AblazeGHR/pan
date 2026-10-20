# Native TUI 双向同步交付（2026-10-20）

隔离树 `D:/project/pan-worktrees/native-tui-sync-20261020`，分支 `fix/native-tui-sync-20261020`，基线 `ccc1ca5b65c4c896dd16934a132a6a169c7b16ce`。本树提交供 MA 验收后快进；未移动 main/practical，未 push、merge、release、重启既有服务或访问 8767/8768。未访问用户既有 Session 或原生历史，也未触碰 workdir-retention 任务。

## 根因与症状对应

以下缺陷由本树故障注入/协议回归实测证实；没有读取用户出问题的 Session，不能声称已证明其生产现场恰好触发了其中哪一项。

| 缺陷 | 可造成的症状 | 修复与证据 |
| --- | --- | --- |
| `ResidentTui.observe` 同步向显示连接发送通知，发送异常冒出 `_read_native`，主事件泵终止 | Terminal 的后续 turn/item/result 不再入 Pan；Pan 后续消息也失去向 TUI 镜像的通道，浏览器刷新无法重建已经中止的泵 | 将显示发送故障限制在显示层，发布原生错误并清除显示 ready，继续主事件读取。owner/kernel 不变；不自动重建 kernel，不伪称损坏显示已恢复。修前 `repro-display.log` 失败，修后 `test_failed_terminal_display_does_not_disconnect_authoritative_history` 通过 |
| `turn/completed` 已带 items，却只取最后 agentMessage 更新 result，未补缺少的 item/completed | lastResult/summary 更新，但持久 history 缺用户、assistant、tool、thinking 内容 | 完成快照逐 item 走现有内容投影，以 turn/item ID 阻止重复完成/快照重放。相同正文的不同 item 保留。修前 `repro-items.log`，修后含完整 thinking/tool、重复重放的测试通过 |
| `session.updated` 用 summary 的 revision/total 充当未 hydrate 的 history 基线 | summary 正确且版本相同，聊天区仍旧/空白；未触发历史读取 | 缺少 canonical window 或窗口无行但 total>0 时仍 hydrate；epoch 改变也触发已有事件驱动读取。沿用既有请求合并，无新轮询/刷新/切 Session 修复。修前 `repro-hydrate.log`，Codex/CBC 同版摘要和空窗口分支均通过 |
| 原生用户写入只保留正文/source；流式及持久 assistant 丢 turn 身份 | 附件输入元数据、nativeItemId/turnId 丢失，重复事件与迟到事件难以归属 | Codex 投影保留原始 nativeContent、item/thread/turn 和 admission 身份；Worker 核验当前 run/owner/thread/turn，按原生 turn+item 拒重复，不按正文去重；先保存再发布用户历史变化。CBC 补原生用户/工具/回复 turn 身份，前端保留 live turnId |

`nativeContent` 保存原生图片/路径等输入元数据；不会将原生路径擅自登记成 Pan upload/server-file 的附件授权。既有 Pan 消息的 messageId、queueItemIds、clientMessageId、taskId/source、parts/envelopes 保留；未引入按正文全局去重。

## 调查结论与边界

- 当前 Codex 0.163.0 的主订阅确实能接收 peer 发起的同 thread turn：修前原有 3 条真实 CLI 隔离测试通过；补入“已 attach 后第二条 Pan 消息”屏幕断言也通过。因此没有猜测性地镜像两份通知或改成第二次 resume。`resident_tui` 辅助请求/响应路由和主订阅的原有分工保留。
- 已读 canonical `.workflow/constraints.md`、`CODEX_NATIVE_CONTROL_20261010.md`、adapter 特殊行为文档，以及原生 admission/多 operation 的现有实现与测试。旧报告中的历史限制不当作当前实现权威。安装 CLI 的 `generate-json-schema` 实测 exit 0，导出 318 文件；`Turn` 必需 `id/items/status`，允许完成通知携带 items。摘录和摘要见 `schema-findings.json`。
- Codex 的运行时同步经 app-server 通知，不依赖 rollout 增量 offset 或定时扫盘。`sessions.py` 原生导入每次全读 JSONL，无增量 offset/metadata history cache；补测了碎片行完成、文件替换和 foreign session 拒绝，没有发现同类游标遗失。无需引入另一个 reader 或定时轮询。
- CBC native 为单个 resident PTY kernel，Pan 投递直接输入同一 TUI；hook 通道处理 admission、UserPromptSubmit、工具和 FinalStop，没有 Codex 的“双连接显示发送异常中止主订阅”结构。共享的 Worker 用户身份丢失和前端 hydrate 缺陷已一并修复。现有 continuation、迟到 hook、partial write、FinalStop、队列和旧 generation 测试保持。
- 没有把 PTY write receipt、turn started、summary 或模型收到消息当作完成证明。真实浏览器探针分别检查另一端屏幕、Pan HTTP history、实际 WS 更新及最后磁盘 history。

## 验证结果

证据根：`audit/native-tui-sync-20261020/`。最终命令 exit 均为 0。

| 层次 | 结果 | 证据 |
| --- | --- | --- |
| 修前定向失败 | 显示连接故障 1 失败；原生输入元数据/完成快照 2 失败；同版 summary 未 hydrate 1 失败 | `repro-display.log`、`repro-items.log`、`repro-hydrate.log` |
| 后端定向 pytest，13 文件 | **217 passed，0 failed/error/skip** | `backend.xml`、`backend.log` |
| 前端 Vitest，7 文件 | **178 passed，0 failed/skip** | `frontend.log` |
| standalone typecheck | `pnpm exec tsc -b` 通过 | `typecheck.log` |
| 定向 lint | hook、对应测试、types 和浏览器探针通过 | `lint.log` |
| build | `pnpm run build` 通过，验证 72 个压缩 production assets | `build.log` |
| Codex 实际 CLI + 生产 React + 实际 Pan HTTP/WS + Chromium | 4 回合；Pan → native → Pan → 刷新后 native；同 thread/wrapper；磁盘 8 行、revision 8；4 个回复实际渲染；0 pageerror | `codex-browser-final.log`、`codex-browser.png` |
| CBC 实际 hy3 + 生产 React + 实际 Pan HTTP/WS + Chromium | 同上 4 回合、8 行、revision 8；同 cliSessionId/wrapper；0 pageerror | `cbc-browser-final.log`、`cbc-browser.png` |
| CBC 实际 hy3 + 原有 native API/Chromium 探针 | 运行中切换、刷新保 owner、queue gate、resize、旧 generation 拒绝、原生 Steer 明确不支持；同 kernel PID/创建身份 | `cbc-real-utf8.log` |

后端覆盖 native admission、completion、transport、passthrough、control owner、auxiliary operation、原生 API、large resume、原生 history/reimport、Worker 集成和 Steer/history 一致性。前端覆盖 live/reconnect snapshot、迟到/交错/重复事件、摘要与历史基线、native control 状态和既有终端输出调度。未运行本地全量。

真实 Codex 使用临时 CODEX_HOME、占位凭据和本地合成 Responses provider；**不是付费 provider 的验证**。真实 CBC 使用用户指定的 `hy3`、新临时 CODEBUDDY_CONFIG_DIR/project profile；不切账户、不改全局配置。探针自身创建所有 Session/HTTP listener/CLI，只收尾自己的资源。

最终 Codex browser：端口 **52829**，wrapper PID **35616**，thread `01a15899-d476-7d70-a936-6b43b7d83cbf`。最终 CBC browser：端口 **54246**，wrapper PID **20004**，cliSessionId `pan-ad41f94748254486a6a679be96d41dd2`。进程已由各探针自己的生命周期收尾，不是常驻验收服务。

### 可重复命令

```powershell
python -X utf8 scripts/run_tests_isolated.py -- python -X utf8 -m pytest tests/test_codex_native_completion.py tests/test_codex_native_transport.py tests/test_codex_native_kernel_real.py tests/test_codex_native_admission.py tests/test_codex_native_passthrough.py tests/test_cbc_native_bridge.py tests/test_native_control_api.py tests/test_codex_worker_integration.py tests/test_codex_auxiliary_operations.py tests/test_codex_large_resume.py tests/test_codex_reimport_origins.py tests/test_codex_control_owner.py tests/test_steer_history_consistency.py -q --junitxml=audit/native-tui-sync-20261020/backend.xml

# packages/web
pnpm exec vitest run src/hooks/useWebSocket.test.tsx src/stores/nativeControlStore.test.ts src/services/nativeTerminalPolling.test.ts src/stores/sessionStore.interleaving.test.ts src/stores/sessionStore.live.test.ts src/stores/sessionStore.refresh.test.ts src/stores/sessionStore.snapshot.test.ts
pnpm exec tsc -b
pnpm exec eslint src/hooks/useWebSocket.ts src/hooks/useWebSocket.test.tsx src/types/index.ts e2e/codex-native-sync-real.e2e.mjs
pnpm run build

# repo root；第二条会调用实际 hy3，共四个短回合
python -X utf8 scripts/run_tests_isolated.py -- python -X utf8 tools/probe_codex_native_sync.py
python -X utf8 scripts/run_tests_isolated.py -- python -X utf8 tools/probe_codex_native_sync.py --cbc
python -X utf8 scripts/run_tests_isolated.py -- python -X utf8 tools/probe_cbc_native_worker.py --real-hy3 --browser
```

### 环境与失败记录

- canonical node_modules 缺 xterm；本树原 junction 改为本地 overlay，其余依赖仍只读复用 canonical，`@xterm/xterm 6.0.0` 和 `addon-fit 0.11.0` 复用已有 `cbc-terminal-sol-20261013` 依赖；不安装/修改共享依赖。
- pytest 环境未安装 pytest-timeout 插件，产生原有 `Unknown config option: timeout` 警告；没有靠该插件保证超时，新真实探针均有显式有界等待。构建保留现有 dynamic-import/chunk-size 提示，未放宽阈值。
- 扩大后端批次曾有 **216 passed / 1 failed**：auxiliary provider 前读盘断言撞到 Windows 元数据原子替换，抛 PermissionError。保留 `backend-windows-read-failure.*`。修复测试读盘使用既有 per-Session ordered persistence ticket，原 durable-before-provider 断言保持；最终 217 项通过。
- 初期浏览器探针的 gateway lifespan 未初始化、刷新后未重新选择 Session、断言把 sidebar preview 也计为回复，以及 Windows GBK 日志错误，均是验收工具问题；修正了探针接线和断言，未据此修改产品逻辑。CBC 初期宽泛提示导致模型输出较长而超过探针等待预算，后用临时 Session system prompt 将答复约束为 DONE。未把这些失败算成产品修前复现。

## 未验证及保留限制

- 未访问/恢复用户已损坏的历史；本修复不自动回填过去已漏写的行，也不自动重启已断开的 resident/kernel。显示断开明确报错，主事件持久化继续。
- 实际付费 Codex provider、真实 provider 下的复杂工具/附件/推理与 Steer 组合、网络断开/服务重启的完整浏览器矩阵、长稳/超大历史和跨平台未做全链实测；相应 owner/reconnect/内容投影/Steer 风险由列出的定向回归覆盖，不能提升为真实 provider E2E。
- CBC hook 当前只给最终 assistant 文本和工具事实，已有的 16k 文本/大型工具有界投影与 hook 不提供 reasoning 的限制仍存在；没有宣称原生 CBC 全量 transcript/thinking 镜像或任意大内容已完整验证。本批解决共享同步缺陷，未替换 CBC 运行时/历史协议。
- 本轮不添加静默重发，不新增定时扫描，不用切 Session/刷新掩盖漏同步，不把 summary 更新当作 history 同步成功。MA 验收与后续快进仍是独立步骤。
