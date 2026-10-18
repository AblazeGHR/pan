# Legal Offline / Multi-operation 人工验收清单

适用提交：`3a2a7c5da9c4914bc874260d153e6434cfe03647`。验收应在本工作树进行，使用动态 loopback 端口；不要使用 8767、8768 或既有 Pan 服务。当前工作树的 `data/` 是服务默认持久化目录，验收会写入 Session、history、日志和 workdir。验收前确认可以接受这些临时记录，完成后从 UI 删除测试 Session 并检查遗留文件。

## 0. 前置检查

在工作树根目录执行：

```powershell
$ErrorActionPreference = 'Stop'
$py = 'D:/project/Pan-main/.venv/Scripts/python.exe'
& $py scripts/check_terminal_deployment.py
if ($LASTEXITCODE -ne 0) { throw 'deployment checks failed' }
node --version
pnpm --version
```

预期部署检查中 `pyte`、`sidecar`、`fastapi`、`uvicorn`、`websockets`、`mcp`、`cbc_command` 均为 `true`，`ready` 为 `true`。本次已实测满足。

## 1. 启动隔离服务

选择尚未占用且不是保护端口的动态端口：

```powershell
$port = & $py -c "import socket
forbidden={8767,8768,8793}
for _ in range(30):
    s=socket.socket(); s.bind(('127.0.0.1',0)); p=s.getsockname()[1]; s.close()
    if p not in forbidden: print(p); break"
if (-not $port) { throw 'no private port' }
$env:PAN_PORT = [string]$port
$env:PAN_HOST = '127.0.0.1'
& $py main.py
```

保持这个窗口直到验收结束。另一个窗口检查：

```powershell
Invoke-WebRequest "http://127.0.0.1:$port/api/sessions?summary=1" -UseBasicParsing
Start-Process "http://127.0.0.1:$port/react/"
```

如果要后台启动，记录 `Start-Process` 返回的 PID 和日志路径；只允许结束自己启动的 PID。不要运行仓库中的默认端口启动脚本。

## 2. 基础合法性与正常收尾

1. 创建一个本地测试 Session/Worker，确认 Worker 进入 Running。
2. 在 Running 期间刷新 Session 摘要，确认 `legalOffline=false`、`illegalOffline=false`；不能因为 UI 瞬时 Idle 或 CLI 仍存活而出现 Illegal。
3. 让任务正常完成；另测一次 provider 429/可捕获 Error，使 Worker 安全进入 Idle。
4. 预期：每个 operation 自己完成后 `legalOffline=true`，`illegalOffline=false`；保留正常 result/report/history。
5. 若使用原生 Codex，记录 network/服务日志中的 admission、started、result 的同一 `requestId/runId`，确认 provider ACK 前没有 provider 请求。

## 3. Running 外杀与一次事故快照

1. 先准备 A（已有 manual lock）、B/C（未锁）和 D（稍后入队）。
2. 确认 Session 正在 Running 后，只结束自己创建的 Worker/CLI PID，不结束现有 8767/8768 服务。
3. 立即查看摘要：应为 `legalOffline=false`、`illegalOffline=true`；事故批次只包含 B/C，A 保持 manual，D 不应被锁。
4. 重复触发 EOF/刷新/恢复检查，确认批次成员不扩大、不重锁已手动解锁的 B，也不创建第二个 cohort。
5. 记录 `legalityDiagnostics.outstanding`、operation ID、incident/batch ID 和队列 lock source。

## 4. 继任、恢复和并发操作

1. 用户显式 Start/Restart 或先加入新的未锁消息，使有效新 Worker 经 adapter ready 后恢复。
2. 预期只清除仍归属该 Illegal 批次的 B/C；A/manual、报告锁、lease、Pause、Steer/self-consume/unknown gate 不应被清除。
3. 先启动前台 turn，再发两个 command/exec；让前台先完成，确认仍 `legalOffline=false`，辅助 operation 不覆盖 task sequence、last result、report 计数。
4. 逐个收到 command deferred final response 后才转为合法；write/resize/terminate ACK 不应提前退休 command。
5. 重启/刷新页面后检查 execution operation UUID、owner/generation、terminal proof 和 queue revision 未被重建或覆盖。

## 5. Resident Codex 原生路径

使用已安装 Codex 0.162.1 和本地 provider/临时 `CODEX_HOME`。不要使用远端模型。

1. Resident TUI 的 `turn/start`：观察 admission 请求；在 Pan 持久 False、唯一 run 和 native admission ID 落盘前，provider 不应收到请求。
2. ACK 丢失、ACK 迟到、控制 generation 切换、发送异常：发送未知必须保持 False/owner，不自动重投；明确未发送或 provider 明确拒绝才可退休。
3. 活动 turn 内 `thread/compact/start`：请求仍应转发并建立独立 operation；空 response、自动 compaction item 或主 turn 完成不能被猜成该 compact 的终态，应显示 uncertain。
4. detached review：用 legacy history mode 的 thread 验证 `reviewThreadId + turn.id` 绑定和子 thread 完成；主 thread 完成、review start response 或错误 thread 的通知均不能退休。默认 paginated thread 的明确拒绝属于预期 upstream 边界。
5. 未知 passthrough：开启 opt-in 后确认请求原样转发并先建立 opaque responsibility；普通 reply 不自动变 True。已验证 config/read 与纯 config/batchWrite 不应误建执行责任； reload/未知执行方法应保留 opaque。

## 6. Takeover / Stop / Restart

1. Takeover 打开前确认持久 `externalTakeover + ownershipUnknown/ownerId`，第二 writer 应被拒绝。
2. Windows Job 路径执行 Stop/Restart：确认 `TerminateJobObject → ActiveProcesses=0 → owner_retirements proof 落盘 → CloseHandle` 的顺序。
3. 注入或观察 replace 失败、句柄关闭失败：Job/owner 不应被清空；再次 Stop 应使用同一 proof/jobId 重试。
4. 非 Job 根 PID 消失但后代覆盖未知时，应拒绝自动退休并保留 unknown。Safe Idle 与 external owner unknown 可以同时存在。

## 7. 每项记录的通过证据

至少保存：服务 PID/端口、Session ID、Worker ID、operation/request/run UUID、generation/control generation、关键时间、UI 截图或 API JSON、服务日志片段、队列 revision/lock source、最终退出码。分别标记“实测”“代码推断”“未验证”。

建议在验收结束后：

```powershell
git status --short
Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
```

只结束自己启动的服务 PID，删除自己创建的测试 Session/workdir；不要删除工作树原有 data 文件，不要操作既有 listener。

## 已知不能由本清单宣称完成的范围

- compact/opaque 没有 provider 唯一完成身份时只能保持 uncertain；不得按 FIFO 或 UI Idle 猜测。
- Codex 默认 paginated thread 不支持 detached review；legacy 成功路径不代表默认模式支持。
- 外部客户端未经过 Pan 受控代理时，Pan 不能观察每个 turn；unknown owner 不可自动清除。
- 本清单不覆盖远端模型、远端 CI、真实 CBC provider、前端浏览器手工交互性能或生产 8768。
