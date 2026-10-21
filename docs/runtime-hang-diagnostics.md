# Pan 卡顿现场采集

页面壳能打开、Session 列表或历史一直加载时，先采集再停止。普通 `stop.bat` 保持正常退出语义；以下命令在退出前增加采集：

```powershell
scripts\stop.bat --diagnose
```

只采集、不停止：双击 `scripts/diagnose_pan.bat`，或运行：

```powershell
scripts\diagnose_pan.bat --session-id ses_你的会话ID
```

输出为当前检出的 `evidence/diagnostics/日期-时间-随机ID/`。采集器不导入 Pan 应用，不启动、停止、恢复或重启任何 Worker。服务没有响应或已经退出时仍会保存能取得的日志和状态。它不会自动安装工具或提权。

默认复制两处控制台日志以及 Pan 日志的最后 8 MiB，记录源路径、文件大小、偏移、修改时间和捕获片段 SHA-256。`--full-logs` 保存完整日志。它不会覆盖既有证据目录。旧原始日志可能含用户消息，保留在本机，分享前需检查。

## 自动留下的信息

正常通过 `main.py` / `PanServer` 启动后，配置日志目录中新增 `runtime-diagnostics.jsonl`，5 MiB 轮转、保留三份备份。每次运行有独立 instance 标识和 PID。

- HTTP：开始、响应头和最终结束；requestId、路径（不含查询字符串）、状态、响应体字节数、结果和完整发送标志。响应头含 `X-Pan-Request-Id`。`complete=true` 指 ASGI 最后一个 body 帧被传输层接受，不代表浏览器确认收到。
- 执行器：store-read、link-projection、diagnostic-read 的 queued/running、排队等待与执行耗时、操作函数、线程及关联 requestId。取消前尚未运行的任务会退出队列计数；已经运行的线程保留到真实完成。
- 独立 daemon 线程检查主循环心跳和待处理操作。持续两秒未推进或出现慢操作时，最多每十秒保存一次有界线程位置快照；主循环恢复后补记 loop lag 和任务位置。
- 诊断不记录请求体、查询参数、provider 正文、异常正文或栈局部变量；只保留有界队列和函数/文件/行号。
- HTTP 控制台写入在独立 daemon 上处理，最多一项在途；超过 50ms 后不再阻挡请求，忙碌期间丢弃后续控制台行并记录 `http.log_dropped`。结构化 HTTP 证据独立落盘。

`GET /api/diagnostics/runtime` 可在 Session 读取队列被占住时查看上述数据。保存队列仍使用 `/api/diagnostics/persistence`；其锁读取已移到独立线程，不在主循环等待保存锁。

## 服务完全卡住时的外部栈

Python 长时间持有 GIL 时，进程内监控线程也可能得不到运行机会；自动诊断不能保证抓住此时的栈。外部采集器会并行、带超时地探测静态页、health、summary 列表和诊断接口，再对验证了 PID、创建时间与检出入口的主进程取资源样本。

预装可选工具：

```powershell
.venv\Scripts\python.exe -m pip install -r diagnostics-requirements.txt
```

采集器从自身 Python 的 Scripts 目录、检出 `.venv/Scripts` 和 PATH 查找 `py-spy`；存在时尝试三次 `py-spy dump --pid PID`，每次重新验证身份，保留退出码和错误，不把失败当成成功采样。不使用 `--locals`。权限或版本不支持时，会明确留下失败记录，其他证据照常采集。命令依据 [py-spy 官方说明](https://github.com/benfred/py-spy#dump)。

## 如何判读

| 现场 | 优先检查 |
|---|---|
| health、runtime 很快，store-read queued 增长 | 当前 running 操作的线程栈、磁盘/文件锁；区分排队时间和执行时间 |
| 主循环 heartbeat age 增长，多个接口一起慢 | 主线程同步工作、GIL、GC；外部栈优先 |
| provider 原生日志继续，Pan 事件记录不再推进 | link-projection、保存队列、Worker 读事件任务的等待位置 |
| 有 headers、无完整 body | 响应发送、取消/断连、编码后传输；不能只看 HTTP 200 |
| `http.log_slow` / `http.log_dropped` | 控制台/文件输出卡住；检查独立结构化日志 |

不要凭某个队列为单线程就增加线程数。单线程读取是现有存储契约；当前补丁保留它，补齐观测，并把完整列表中的链接路径处理移出主循环。

## 本次事故的结论边界

2026-10-21 停止后的 111 个 Session 离线重放没有复现持续卡死。已验证的隐患是：完整列表在主循环上处理本地链接；原链接正则对大量未闭合方括号存在平方耗时；响应头前等待默认线程池输出日志；保存诊断接口在主循环上取锁。补丁分别移出链接 I/O、替换为兼容旧语法的线性扫描、隔离并限制控制台输出、异步读取保存计数。这些修复不能证明本次事故由其中某项触发。
