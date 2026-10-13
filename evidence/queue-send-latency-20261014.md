**T-QUEUE-SEND-LATENCY-20261014/1：实现完成，隔离验收通过，待 MA 验收与主线集成。**

工作树 `D:/project/pan-worktrees/queue-send-latency-20261014`，分支
`fix/queue-send-latency-20261014`，基线 `1db8d9bd893be7f764619e12bcd2d09a5f3eba00`。
本次只提交该分支的增量；未推进 main/practical、push、发布或操作既有服务。
8768 的监听进程前后均为 PID 28268；未向 8767/8768 发请求。所有服务探针使用
自有数据根、随机空闲端口、去除继承 PAN_* 的环境和本地假 CLI。

**修复及因果证据**

- 编辑租约过期后缺少消费唤醒。`_consumer()` 被租约挡住时，原
  `_schedule_queue_retry()` 只处理 nextAttemptAt，没有租约期限，活 Worker
  watchdog 也不补发该信号。现在将有效租约期限纳入同一个截止时间等待；
  续期重新计算期限，新增更早期限可用 Event 重设等待。没有增加 watchdog
  轮询，也没有把 pending_signal 当成正文队列。
- direct Steer 在队列锁内等待 stdin drain，导致后续 Send 等同一把锁。
  租约检查与同步 stdin.write 仍在一个临界区，drain 移到锁外。普通队列
  handoff 的 write/drain/receipt 锁区保持原样，保留 Pause everything 的既有
  线性化语义；未通过放宽暂停、编辑租约或 owner/generation 门禁提速。
- 带 provider receipt 的 direct Steer 原先先等待 drain，收到接受回执也不能
  立即完成。现在等待 sender/receipt 中先完成者，明确接受回执可完成响应；
  取消后仍保留原请求的迟到回执 owner。缺失或 unknown 回执不恢复重发权限。
  写入前已死亡的进程仍返回确定拒绝，不误报 unknown。
- MCP `_caller_identity()` 使用现有 `view=metadata`，不读取调用方完整历史。
  agent_send 与旧 worker_send 在同一次调用内复用权限检查的身份快照，保留
  sourceSessionId、前缀、managed 隔离及 claim 规则；无跨调用缓存。

**已测：冻结基线与修复后**

| 场景 | 基线 | 修复后 | 证据 |
|---|---:|---:|---|
| 编辑租约过期，无新 Send | 0.8 秒观察窗口内仍未消费 | 过期后 16.3 ms 到达 CLI，原 ID/FIFO 保留 | control JSON |
| direct Steer，测试夹具阻塞 drain 350 ms：request→ACK | 379.2 ms | 7.2 ms | control JSON |
| 同时追加 Send：request→持久 ACK | 373.0 ms | 4.9 ms | control JSON |
| direct Steer：provider receipt→ACK | 376.6 ms | 3.5 ms | control JSON |
| queued Steer 缺失即时回执：HTTP 等待 | 9,996.4 ms | 10,007.7 ms | 保留原 item，迟到接受回执后送达 |
| direct Steer 缺失回执：HTTP 等待 | 30,298.9 ms | 30,004.8 ms | 均为 unknown/do not resend |
| unknown 后再发 Steer | 1.6 ms 拒绝 | 1.5 ms 拒绝 | 假 CLI 未收到第二次 control |
| agent_send，调用方 8,000 条合成历史，n=6：p50 | 329.9 ms | 28.1 ms | MCP JSON |
| 旧 worker_send，同上，n=6：p50 | 382.6 ms | 36.1 ms | MCP JSON |
| 普通 idle Send，n=12：HTTP ACK p50 | 6.0 ms | 4.7 ms | queue JSON |
| 同上：request→CLI p50 | 12.4 ms | 9.3 ms | queue JSON |
| 200 次历史填充后 idle Send，n=12：HTTP ACK p50 | 78.0 ms | 49.6 ms | queue JSON |
| 同上：request→CLI p50 | 95.2 ms | 60.6 ms | queue JSON |

control 夹具仅在 `PAN_E2E_LATENCY_CONTROLS=1` 且指定 fake CLI 时启用：将测试
编辑租约缩为 150 ms，并明确注入 350 ms drain 阻塞。生产租约仍为 5 分钟，
HTTP/receipt 的 10/30 秒上界没有缩短。假 CBC 测试 adapter 宣告 receipt 能力
仅为了覆盖 Pan 的共同路径；这不是实际 Codex/Claude/CBC 原生 provider 验收。

queue JSON 同时记录 HTTP ACK、WS item_added、CLI received、WS item_delivered
及持久化诊断 lastWaitMs/lastFlushMs。两次填充后均有 690 条历史、230 条 ledger
回执、0 pending；4 条 busy 积压按 FIFO 消费。保存等待 p50 均为 0 ms，保存
flush p50 约为 15–16 ms。诊断是请求后采样的最近保存值，并不等于该请求独占的
保存操作；Windows 时间粒度及单组样本会带来抖动。普通 idle/填充数据的变化
属于前后观察，不能单独归因为本次锁或唤醒修复，也未复现普通短历史 Send 的
秒级延迟。新增红测与 350 ms 阻塞对照才是 direct Steer 锁竞争的因果证据。

MCP 探针调用注册工具函数，实际经过 HTTP 回环和真实假 CLI 进程；未包含
stdio transport 建连与 framing。managed 目标路径的 agent_send 从两次完整
调用方 GET + POST 减为一次 metadata GET + POST；旧 worker_send 另保留必要的
worker→Session 解析 GET。请求分段耗时和路径保留在 MCP JSON 中。

**证据入口**

- [control 基线](queue-control-latency-baseline.json) / [最终修复](queue-control-latency-final.json)
- [MCP 冻结基线](mcp-send-latency-baseline-final.json) / [最终修复](mcp-send-latency-final-mcp.json)
- [queue 冻结基线](queue-latency-probe-baseline-timed.json) / [修复后](queue-latency-probe-after-timed.json)
- [最终 JUnit](queue-latency-final-tests.xml) / [完整日志](queue-latency-final-tests.log)
- [队列/Steer 原始三个红测 XML gzip](queue-send-latency-artifacts/queue-send-latency-red.xml.gz) / [MCP 两个红测 XML gzip](queue-send-latency-artifacts/mcp-send-latency-red.xml.gz)
- [校验清单与命令](queue-latency-evidence-manifest.json) / [CLI 原始记录副本](queue-send-latency-artifacts/)

冻结基线来自 `git archive 1db8d9bd`，仅复制探针脚本及 test-only launcher/fake CLI。
基线 worker.py 和 MCP server.py 与该 Git blob 在统一 LF 换行后逐字节一致。
探针 JSON 中的 sourceSha256 是实际执行文件的原始 bytes 哈希；Git archive
在本机导出 CRLF，不能直接用 raw hash 与 LF blob hash 比较。源码、报告、XML、
CLI 副本的哈希与基线一致性事实在 manifest 中保存。
红测 JUnit 含 pytest 原始 traceback 的行尾空白，以 gzip 封存完整原始 bytes，
原 XML 本地保留；未修改失败信息或 test identity。manifest 同时记录压缩文件
与解压后 XML 的 SHA256；其余文本文件还提供统一 LF 后的哈希，便于跨 checkout
核对 Git 的换行转换。

测试夹具同时补齐 config、Jobs、scheduler、Terminal 的隔离根，避免测试入口
虽然换了 Session 根却仍使用 checkout 默认配置/注册表。原探针先修正混用
time.time/perf_counter、busy 阶段先等待消费才放行、诊断字段读取错误和进程
清理问题，再取基线；早期 HTTP 夹具参数错误的尝试不是成功测量，原目录保留。

**验收与复跑**

最终执行 18 个相关测试模块：346 passed，0 failures，0 errors，0 skipped；
新增 12 项覆盖原三个红测、租约续期、Steer 取消后的迟到回执、unknown 阻止重发、
写前死亡拒绝、MCP metadata/身份/claim/拒绝。原有测试断言、阈值、skip/xfail
和 test identity 未修改。另有两项既有依赖警告（pydantic settings 与
Starlette TestClient 的 httpx deprecation），不影响上述结果。

MA 验收：先核对分支增量及 manifest 哈希，再检查最终 JUnit 的 346 个身份与
零失败/零 skip；对照两组 control JSON 的原 item/unknown/迟到 receipt 和 CLI
副本，最后核对 MCP 请求次数与延迟。接受、失败或另立验证任务的结论回填到
`T-QUEUE-SEND-LATENCY-20261014/1` 的验收记录，并引用本报告与增量提交。
main→practical 的集成由 MA 验收后执行，本次 TA 不执行。

每次复跑使用未存在的 label。完整 argv、cwd 在 manifest；核心入口示例：

```powershell
python scripts/run_tests_isolated.py -- uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt -- python tools/probe_queue_send_latency.py --label review --rounds 12 --backlog 4 --history-filler 200
python scripts/run_tests_isolated.py -- uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt -- python tools/probe_mcp_send_latency.py --label review --history-rows 8000 --rounds 6
python scripts/run_tests_isolated.py -- uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt -- python tools/probe_queue_control_latency.py --label review --missing-receipt
```

所有验证均为单次有界的前台运行，最长探针约 41 秒；没有启动长时后台 Job，
没有残留探针/假 CLI/隔离服务进程。实测层覆盖单元/权限/生命周期回归、真实
HTTP/WS、MCP 函数回环与假 CLI；未测真实 provider、真实数据/凭据、浏览器实际
点击、主线服务加载或生产性能，不将这些层宣称为完成。普通队列 handoff 的
drain/持久化锁竞争仍是原有边界，本次只修复已证实的 direct Steer 竞争与有限
租约唤醒，不宣称消除任意磁盘/pipe backpressure 引起的全部延迟。
