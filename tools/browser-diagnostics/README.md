# Pan 浏览器消息轨迹：最简操作

这一套工具只在隔离工作树中运行。它启动一个独立的 Chrome 配置文件，连接本机 Chrome DevTools 接口，持续记录 Pan 页面的 WebSocket 帧、前端 store 顺序和实际渲染行顺序。它不会启动、停止或重启 Pan 服务，也不改动日常 Chrome/Edge 配置文件。

## 一键启动

1. 确保平时使用的 Pan 页面可以打开。
2. 双击 [Start-Pan-Browser-Trace.cmd](Start-Pan-Browser-Trace.cmd)。新 Chrome 窗口会自动打开 `http://127.0.0.1:8768/react/?panE2E=1`。若你的 Pan 地址不同，在该窗口地址栏输入平时的 Pan 地址，并加上 `?panE2E=1`（已有查询参数时加 `&panE2E=1`）。请在**这个标签页**复现问题。
3. 看到重复或错位时，回到启动脚本的命令窗口按一次 Enter。脚本会插入时间标记并保存截图；采集此前已经持续进行。复现完输入 `q` 再按 Enter，脚本关闭它启动的 Chrome 并写出结果。
4. 把命令窗口显示的 `Trace directory` 告诉我。我可以直接读取该隔离工作树下的文件；不需要手动复制对话内容。

结果保存在 `packages/web/test-results/browser-trace-<时间>/`（Git 已忽略）。`trace.ndjson` 是逐事件轨迹，`marker-*.png` 是按 Enter 时的截图，`summary.json` 记录本次采集状态。`chrome-profile/` 是本次专用浏览器配置文件，可能包含你在该窗口产生的浏览数据。

## 其他地址或完整内容

若 Pan 不在默认地址，可从 PowerShell 运行：

```powershell
& 'D:\project\pan-worktrees\stream-integrity-20260923\tools\browser-diagnostics\Start-Pan-Browser-Trace.ps1' -PanUrl 'http://你的地址/react/'
```

默认轨迹保存事件身份、序号、文本长度和摘要，不保存 WebSocket 原文或 store 消息正文；截图仍包含屏幕上可见的内容。若诊断需要精确正文及内容前缀关系，在命令后追加 `-FullContent`，原文只保存在本机轨迹目录。

## 我如何实时查看

Chrome 窗口保持打开时，我可以在当前工作环境运行：

```powershell
node tools/browser-diagnostics/inspect-browser.mjs
```

它读取实时页面 URL、当前 Session、store 尾部消息身份和页面行序；加 `--screenshot` 可保存一张当前页面截图。启动脚本会使用 Chrome 自动分配的本机调试端口，并把端口、浏览器 PID 和轨迹目录写入该次的 `active.json`。关闭窗口后 `active.json` 标为离线。

## 适用范围

- 轨迹从启动后开始，无法恢复启动前的 WebSocket 帧。
- `?panE2E=1` 使用 Pan 已有的只读 store 检查入口。若页面版本没有该入口，轨迹仍会有 WebSocket 与 DOM；`attached` 事件缺失会提示 store 未接入。
- DOM 只包含虚拟列表当前渲染的行；store 轨迹保存尾部 120 行及其原始索引。
- 当前脚本跟踪它启动的第一个标签页。若要在另一个标签页复现，请在原标签页直接输入 Pan 地址，或重新启动脚本并传入 `-PanUrl`。
- 如果问题只在 Edge 出现，可以把 `-ChromePath` 设为 Edge 的 `msedge.exe`；其余步骤相同。
