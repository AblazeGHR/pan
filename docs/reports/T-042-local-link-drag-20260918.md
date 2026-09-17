# T-042 本地文件链接拖入附件验收证据

日期：2026-09-18

工作树：`D:\project\pan-worktrees\local-link-drag-20260918`

分支：`feature/local-link-drag-20260918`

基线：`b466a76d714fcd68852d3b466de07f71a180aa65`

## 已确认的链路

`_project_editor_links()` 原先只由 `api_session_history -> _api_history` 调用。实时
`worker.stream`、`worker.result`、重连补发、Session summary 的 `lastMessage` 和完整
Session 的 `lastResult` 没有经过该投影，所以刷新历史后链接可拖，实时/完成结果只能点击。

本任务在 Web 对外出口增加统一的 best-effort 投影：

- 保留 Session 内部 provider/history 原始文本；只投影发给 browser/agent client 的副本。
- `worker.stream` 同时处理字符串内容、`message.content` 字符串/文本 block 和
  `stream_text`；`worker.result` 与重连补发也投影。
- `lastMessage` 在完整 Markdown 链接投影后再截断，避免先截断破坏链接。
- 原始相对路径按来源 Session workdir 解析；服务端生成 `att_...` opaque editor/ref
  引用。拖拽 payload 仍只写安全 href/id，不写 path；`attachmentDrag` 的 path 拒绝逻辑未改变。
- 点击沿用原有 editor 链路；拖动沿用同步 `dragstart` payload，未在 dragstart 中 await 注册。
- 发送继续使用结构化 `parts`，由服务端 canonicalize 为 `source=server_file`；跨 Session
  只复用服务端文件引用，不复制文件字节。目录和不存在文件由已有 endpoint 明确拒绝。

## 自动化证据

- Python：
  - `python -m pytest tests/test_attachment_parts_protocol.py -q`：13 passed。
  - 新增 `test_live_result_and_stream_projection_match_history_projection`，覆盖 stream/result、summary 和 full API 与 history 一致投影。
- 前端：
  - `pnpm exec vitest run src/components/chat/MarkdownRenderer.test.tsx src/components/chat/InputRow.test.tsx src/utils/attachmentDrag.test.ts --reporter=dot`：3 files / 60 tests passed。
  - `pnpm build`：TypeScript 与 Vite build 通过；仅有既有 chunk 大小 warning。

## 隔离真实 HTTP/Chromium 证据

使用本 worktree 的 `packages/web/e2e/server.py`、canonical Python 和仅本任务拥有的
隔离端口 8795；服务 identity 记录为该 worktree，listener PID 为 43532，测试完成后
已按 identity 精确停止，8795 已释放。未操作 8768、D:\project\Pan 或他人进程。

`packages/web/e2e/run-browser.mjs` 结果：

- `sorting short and long pointer press`：passed。
- `drag auto scroll and HTTP order persistence`：passed。
- `markdown files through real editor and external link preservation`：passed。
- `live local link click and real mouse attachment drag`：passed。
  - 通过真实页面注入一条原始 `notes.md#L42-L48` 链接，确认实时结果被投影为
    `/api/attachments/editor/att_...?...#L42-L48` 且 `draggable=true`。
  - 同一链接点击进入 Editor，定位到 42-48 行。
  - 使用 Playwright `locator.dragTo` 实际鼠标拖动到输入框，不是伪造 DataTransfer-only
    事件；输入框生成 `notes.md` 附件节点。
  - 发送响应中的 part 为 `source=server_file`、服务端 opaque `attachmentId`，未携带
    客户端 path/绝对路径；没有 upload request。
  - 拖动后再次点击同一链接仍可进入 Editor。
- `cross-session server-file drag and optimistic send`：passed；跨 Session 复用同一
  服务端文件引用、无二次上传，并保持源 Session history 不变。

完整 harness 还执行了既有 `stream follows exact bottom only`，其既有 1px 离底失败：
`before=1, after=0`。该失败与 T-042 链接投影/附件拖入无关，未修改或吸收。

## 未验证/边界

- 未运行移动端 E2E。
- 未做生产服务或 8768 验证；未启动、停止或操作任何非本任务进程。
- `node_modules` 通过仓库 `tools/prepare_validation_env.ps1` 在本 worktree 建立 junction，目标为
  `D:\project\Pan-main\packages\web\node_modules`，共享依赖未修改。
- 真实浏览器证据覆盖同 Session 实时链接和跨 Session 已投影 server-file 链接；目录/无效
  文件拒绝由既有 Python HTTP/endpoint 测试覆盖，未在本次 Chromium 中额外操作系统文件选择器。
