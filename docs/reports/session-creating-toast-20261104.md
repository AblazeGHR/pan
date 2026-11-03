# Session 创建期间的不存在 toast 修复

候选分支：`fix/session-creating-toast-20261104`。实际起点 `1430c99ac8d87efa43fff84f67be5777f1dcc961`；由 MA 决定整合 practical。TA 不推进 practical/main/X5、不 push、不重启真实服务。

## 根因与契约

`createQuickNewSession` / 创建表单调用 `sessionStore.createNewSession`，先发布并选中 `__pending_*` placeholder，再 POST `/api/sessions`。React 立即进入空聊天页面。`InputRow` 冷读草稿调用 `loadDraft` → `getSessionDraft`，持久层对客户端身份返回结构化 `Session does not exist`；草稿错误订阅调用原 showToast。这不是页面存在检查产生的 toast。

同一边界还包括书签、queue、延迟重启、CLI takeover readiness、Terminal TUI 状态、历史恢复与 WS resync。基线浏览器记录证明这些读取会把 placeholder 当成持久身份。列表刷新还会丢弃 placeholder 并清空选择，原成功归并把 null 当成仍选中，可能抢回用户取消后的选择。

保留现有乐观创建与页面立即呈现。集中记录本次运行创建的客户端身份，只有这些已知身份跳过持久读取/WS 目标；不按错误文本吞错，不改变 API/后端 schema，不加 sleep/retry。未注册的 pending 外观字符串仍走正常 HTTP 错误。成功后使用原服务端返回身份，正常重新查询。并发同名创建的 placeholder 唯一；列表刷新保留正在创建的对象；只有仍选中的那次创建可接管选择和 loading。失败移除自己的 placeholder、抛出原错误。删除事件/显式删除发生在创建响应之前时，迟到响应不能复活该身份。

客户端身份的来源记录在当前页面运行期保留，用于挡住卸载清理的迟到 draft 请求。删除竞争标记仅在有创建进行时记录，全部创建结束后清空。真实 Session 的错误处理不变。

## 文件

- `packages/web/src/stores/sessionCreation.ts`：创建身份来源、pending 与删除竞争标记。
- `packages/web/src/stores/sessionStore.ts`：创建/列表/选择/历史/删除归并。
- `packages/web/src/stores/composerDraftStore.ts`、`bookmarkStore.ts`、`queueStore.ts`、`workerStore.ts`、`nativeControlStore.ts`：持久读取和迟到草稿写入的身份边界。
- `packages/web/src/hooks/useWebSocket.ts`、`useTakeoverReadiness.ts`、`useTerminalTuiEntry.ts`：WS 目标和读取生命周期边界。
- `packages/web/src/stores/sessionStore.creating.test.ts`、`sessionStore.race.test.ts`：针对性回归与负控。
- `packages/web/e2e/session-creating-server.py`、`session-creating.e2e.mjs`：可重跑的真实 HTTP/WS/production Chromium 时序 harness。

## 验证

首次针对性红灯：延迟 POST 时 `loadDraft(placeholder)` 通知 `Session does not exist`，断言 errors=[] 失败。修后通过；未注册身份真实 404 和真实身份结构化不存在错误保留为负控。并发两创建、切走/null 取消、迟到成功/失败、删除先于响应均有回归。

同一浏览器 harness 对原 HEAD 源码重新构建，结果为红：
`packages/web/test-results/session-creating/1793722671988/evidence.json` 记录 toast `Session does not exist` 及 placeholder 的草稿、书签、queue、native-control、takeover、delayed-restart HTTP 请求。构建日志 `session-creating-red-build.log`；失败日志 `session-creating-red-browser.log`。验证结束已还原候选源码，并重新正常构建，未手改 dist。

最终检查命令（工作目录 packages/web）：

```text
pnpm exec vitest run src/stores/sessionStore.creating.test.ts src/stores/sessionStore.race.test.ts src/stores/composerDraftStore.test.ts src/stores/bookmarkStore.test.ts src/stores/queueStore.test.ts src/hooks/useWebSocket.test.tsx src/hooks/useTakeoverReadiness.test.ts src/stores/nativeControlStore.test.ts src/stores/workerStore.test.ts
pnpm exec tsc --noEmit
pnpm build
node e2e/session-creating.e2e.mjs
```

Vitest 9 文件 / 211 项通过，退出 0。原 WS fixtures 的 loadSessions warning 保留。typecheck 退出 0。build 退出 0，包含 tsc -b、Vite 和 90 个预压缩资源核验；保留原 bundle size/dynamic import warning。日志位于 `packages/web/test-results/session-creating-{vitest,typecheck,build}.log`。

production Chromium 使用全新 context、自有动态端口和合成 runtime，真实 FastAPI 创建/持久化和 /ws 广播；用两道 Event gate 分别控制提交前、WS 后 HTTP 响应前，不使用 page.route/mock transport。fixture 不加载插件，显式空 MCP，仅创建不启动 Worker，无真实 provider。成功断言立即页面、零 placeholder HTTP、WS 先于响应、原服务端身份最终选中并读取 draft；失败原错误可见，切走后的成功/失败不夺回选择，WS 发布后删除不复活，真不存在 draft 错误保留，navigation 返回真实 404。

浏览器早期实验失败也保存在各自 runtime：fixture 请求结构错误、空 MCP catalog 配置缺口，以及误把现有 history 的结构化 error/HTTP 200 当成 404。已修正 harness，404 负控使用原 navigation 接口；未改产品错误语义。

## 边界

这是本机 Windows 的源码、单测、生产构建和隔离真实 HTTP/WS/Chromium 候选证据。没有远端 CI、Linux/macOS、真实 profile/provider、部署或人工验收结论；没有把候选视为 practical 已整合。测试没有使用 8767/8768，没有触碰真实服务或数据。MA 需按最终提交独立整合验证和 build。
最后一次 fresh production Chromium：`packages/web/test-results/session-creating/1793722885442/evidence.json`，`result=passed`，`serverExited=true`、`portClosed=true`；含 `final.png` 和 `server.log`。此前一次启动后立即探测端口存在短暂退出竞态，现用有界端口关闭断言确认清理；所有既往测试端口也已只读核验没有监听者。
