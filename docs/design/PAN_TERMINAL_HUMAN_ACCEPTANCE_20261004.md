# 人工验收入口（隔离集成候选）

工作树：`D:/project/pan-worktrees/terminal-final-integration-ma-20261004`

分支：`integrate/terminal-ma-final-20261004`。以最终交接提交为准，不切换 main/practical。
React 产物已构建在 `packages/web/dist/`；sidecar exact-pin 依赖已在专属目录安装。
这两处是本机 ignored 产物，不是提交到仓库的运行环境。

## 本机启动参考（由验收者执行，本轮未启动完整 Pan）

在独立 PowerShell 窗口中执行。示例使用 8767；先确认端口未被占用，若占用则
换一个未使用的 loopback 端口，并同步修改两项环境变量及 URL。不要停止别人的
监听者，不使用现有 8768。变量只影响此窗口；不要把终端目录指向线上数据。

```powershell
Set-Location D:/project/pan-worktrees/terminal-final-integration-ma-20261004
Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue
# 上面应没有监听结果；若有结果，停止此流程并换一个空闲端口。
$env:PAN_PORT = '8767'
$env:PAN_API_URL = 'http://127.0.0.1:8767'
$env:PAN_TERMINALS_DIR = 'D:/project/pan-worktrees/terminal-final-integration-ma-20261004/data/terminals-human-acceptance'
uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt -- python -m uvicorn packages.web.server:app --host 127.0.0.1 --port 8767
```

打开 `http://127.0.0.1:8767/react/terminals`。完整 Pan 使用本工作树的配置与 data，
不代表已部署到原服务。不要拷贝/共用线上 data 来快速尝试；真实 CBC Rewind
请使用你主动创建的测试 Session 和可丢弃工作目录。完整 lifespan/插件是否符合
你的实际配置，仍属于此次人工运行验收，不由隔离 browser harness 代替。

## 终端检查

1. 创建终端，指定临时工作目录，确认提示符与 cwd 正确；获取控制权。
2. 输入中文并运行简单命令，确认显示、复制粘贴与键盘交互；启动可中断的
   测试命令验证 Ctrl-C，不把该结果外推到所有 CLI/TUI。
3. 改变窗口大小，刷新/关闭再打开浏览器；检查输出恢复与明确的降级提示。
4. 第二个页面观察、抢占；旧页面不能继续以旧控制权输入。
5. 在支持的宿主布局中 detach/reconnect；若 Job 限制导致拒绝，应保持原状态，
   不把拒绝当作成功 detach。最终主动关闭测试终端。
6. 输入 `echo FINAL_中文&exit 7`（默认 cmd shell）：最终尾部保留、列表 exited、
   退出码 7、输入禁用；再次选择退出记录应提示无法重连旧进程，不伪造归档屏幕。
7. 退出测试服务前主动关闭所有测试终端；确认关闭失败时可重试、不是假 exited。

## Rewind 检查（真实 CBC 能力尚需验收）

创建自己的可丢弃 CBC Session，以 CBC 文件编辑工具修改一个测试文件；等待
Worker 空闲。Chat 用户消息旁的「撤回」入口打开弹窗，包含「对话 + 代码」、
「仅对话」、「仅代码」三种范围及进度。它创建分支 Session，原会话保持不变。
优先在测试目录验证三种范围、分支历史、文件状态与失败提示，切勿拿重要目录
做首次验证。Bash 或手动改写的文件不承诺回滚。现有自动化真实 PTY 用例使用
模拟 CBC 菜单，不能代替此次真实 CLI/账户路径。

## 证据及边界

最终自动化结果见 `PAN_TERMINAL_FINAL_CLOSEOUT_20261004.md` 与
`PAN_TERMINAL_CURRENT_MAIN_INTEGRATION_MA_20261004.md`。历史失败 XML 保留，
不倒写历史。候选包含功能/UI/PR6 Rewind；不表示 main 已合并、服务已重启或
provider/跨用户/跨平台/所有 TUI/所有 Job 布局已验收。
