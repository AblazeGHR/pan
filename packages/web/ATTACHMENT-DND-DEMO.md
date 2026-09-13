# 附件拖拽到消息输入框 Demo

这是一个附件交互 demo。使用 `?mock=1` 时启用完全前端的 mock 数据层，不需要、也不会连接 Pan 服务；去掉该参数时，附件上传和消息队列走现有真实 API。

## 启动

在当前 worktree 执行：

```powershell
cd D:\project\pan-worktrees\attachment-dnd-ui-demo-20260913\packages\web
pnpm install --frozen-lockfile
pnpm dev -- --host 127.0.0.1 --port 5173
```

打开 <http://127.0.0.1:5173/?mock=1>。只有 URL 的 `mock=1` 会启用无后端演示；不带该参数时仍使用原有 API/真实上传实现，需要已有 Pan Web API 服务提供 `/api`。如果浏览器保留了旧的 mock 数据，点击左下角 `MOCK DEMO · 无后端`，再点击“重置 Demo 数据”。

真实模式的客户端上传仍使用 `POST /api/sessions/{sessionId}/attachments` 原有接口。响应中的 `attachmentId`/`storageFilename` 是服务端 opaque 稳定引用，`displayName` 只用于显示和 Markdown 标签，`href` 用于受限下载；发送时沿用既有 Markdown 文本入队协议，服务端会再次校验内部附件引用属于当前 session 且文件仍存在。

Vite 启动后，可在另一个终端执行两条真实 Chromium 回归路径（脚本不会启动后端或 Vite）：

```powershell
cd D:\project\pan-worktrees\attachment-dnd-ui-demo-20260913\packages\web
pnpm e2e:attachment-dnd
```

脚本会实际输入文字、拖动消息附件到中间、再用鼠标把已插入节点拖到另一段文字中间；会检查 DOM 文本/节点数量、selection、dragover/drop 是否被接受和插入指示线，并将截图与事件日志写入 `test-results\attachment-dnd-browser`。

## 操作

1. 在左侧选中 `Alpha 主控` 会话。
2. 在消息区找到带文件图标的 `接口说明.md`，拖动它到输入框文字中间。
3. 在文字之间移动时，蓝色竖线表示释放位置；释放后会出现带文件图标和文件名的附件节点。
4. 再次拖动输入框中的附件节点，可以把它移动到其他文字位置；节点不会复制或丢失。
5. 点击节点右侧的 `×` 可整体删除；也可以把光标放在节点前后继续输入。
6. 使用 `Ctrl+A` 后按 `Backspace` 会删除编辑器内容和其中的嵌入附件；附件不会错误回到上方 chip。普通 Backspace/Delete 或 `×` 也按原子节点删除语义处理。没有拖入编辑器的附件 chip 仍会随 `Send` 发送。
7. 点击 `Send`，mock 队列中会显示按光标位置排列的 Markdown 兼容文本，便于观察序列化结果。
8. 点击 `Queue` 可以展开当前页面的 mock 待发送队列，验证队列行、编辑、排序和删除；该队列只用于本次页面演示，不会写入后端。

也可以验证一次性客户端上传：点击输入框右上角回形针，选择“客户端附件”，选择一个本地文件；观察“上传中”到“已完成”的进度，再把完成后的附件 chip 拖入文字中，最后点击 `Send`。
可以一次选择多个文件；同一选择中的重复文件会去重。上传尚未完成时点击对应的“取消附件”可验证取消不会在稍后恢复 chip。

## 限制

- `?mock=1` 只演示浏览器拖拽、插入、编辑、删除、一次性 mock 客户端上传和页面内 mock 队列；mock 附件节点不会持久化。
- 普通真实模式复用 session 附件上传、受限下载、消息 queue 和 Session history；上传文件按 session 隔离持久化，消息中的 Markdown 附件引用随 queue/history 持久化。
- mock 会话排序会因浏览器 localStorage 保留；队列和输入附件仅存在于当前页面。用左下角重置按钮可恢复演示数据。
- `pnpm dev` 只启动 Vite 前端；真实模式还需要现有 Pan Web API，完整 browser 矩阵和 mobile E2E 仍不在范围内。
