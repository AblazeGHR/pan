# 附件拖拽到消息输入框 Demo

这是一个仅前端的交互 demo。它使用 `?mock=1` 启动已有的 mock 数据层，不需要、也不会连接 Pan 的 8768/8767 服务。

## 启动

在当前 worktree 执行：

```powershell
cd D:\project\pan-worktrees\attachment-dnd-ui-demo-20260913\packages\web
pnpm install --frozen-lockfile
pnpm dev -- --host 127.0.0.1 --port 5173
```

打开 <http://127.0.0.1:5173/?mock=1>。如果浏览器保留了旧的 mock 数据，点击左下角 `MOCK DEMO · 无后端`，再点击“重置 Demo 数据”。

## 操作

1. 在左侧选中 `Alpha 主控` 会话。
2. 在消息区找到带文件图标的 `接口说明.md`，拖动它到输入框文字中间。
3. 在文字之间移动时，蓝色竖线表示释放位置；释放后会出现带文件图标和文件名的附件节点。
4. 点击节点右侧的 `×` 可整体删除；也可以把光标放在节点前后继续输入。
5. 点击 `Send`，mock 队列中会显示按光标位置排列的 Markdown 兼容文本，便于观察序列化结果。

## 限制

- 当前只演示浏览器拖拽、插入、编辑和删除；附件节点不会持久化，也没有上传、后端协议或真实发送链路。
- mock 数据可能因浏览器 localStorage 保留会话排序和队列状态；用左下角重置按钮可恢复演示数据。
- `pnpm dev` 只启动 Vite 前端；真实 browser/mobile E2E 不属于本 demo 的自动化验证范围。
