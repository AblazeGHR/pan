# MA 退回后的首条 terminal 丢失修复

本次以 `4b1453f7d199a10aebe749d966883dd08672897b` 为父提交继续。完整保留 MA 的 off-loop probe 修改、5 个回归和六份过时根报告删除；未 reset、追 main 或移动其他分支。本报告补充并修订原 REPORT.md 的验收状态，提交完成不等于 MA 已验收。

## MA 原失败证据

已在运行新 pytest 前复制 `C:/Users/14709/AppData/Local/Temp/pytest-of-ablaze/pytest-3137/test_http_takeover_controls_re1`，见 [原始快照](ma-failure-3137/test_http_takeover_controls_re1/runtime/sessions/ses_b483f2723e97eabb.json) 和 [9 个文件 SHA256 清单](ma-failure-3137/SHA256.json)。另提供逐文件 SHA256 校验的 [原始字节 ZIP](ma-failure-3137.zip)，用于避免 Git checkout 的文本换行转换影响原始证据。

- fake CLI PID 42088 于 21:17:10.251 收到 `establish-native-session`，工作目录归该临时根；不是 admission 拒绝或未建立 CLI。
- 持久 metadata 有 `sent_to_cli` receipt，队列已移除；history 有 user、`ack:establish-native-session` 和 result 文本三行。`last_result=null`、`result_cursor=0`、terminal_results 为空、foreground 责任仍 running。没有进入 takeover/Restart。
- `pan.log` 是原始 0 字节文件。缺少当次异常栈，因此不能把后续实验的异常栈冒充当次直接证据。
- fake CLI 的成功 ASCII init/received 和落盘的两条 assistant 表明该消息已往返解析；GBK/UTF-8 不是这条 ASCII 消息未送达的解释。

## 判别实验与根因边界

在只增加终态异常记录的 probe 后，对失败参数做了最多 10 次有界诊断运行，全部通过，没有捕获自然失败；这些重复通过不作为关闭依据。原始诊断文件均压缩保留在 [pre-fix-instrumented-runs.zip](pre-fix-instrumented-runs.zip)，164 个文件逐字节校验后归档，SHA256：`1a3cf7753bef1d3c7bdfe458ada6a1ed33f411b5191ceec8c2cc9a8f2c65c052`。

随后定向验证了 `durable(runtime, sid)` 使用的 Windows 普通读句柄与终态原子替换的相互作用：真实打开 Session metadata 供读，在实际 `os.replace` 被内核拒绝后才释放读句柄。修复前 **1 failed**，真实异常为 `PermissionError [WinError 5]`，路径为 `_persist_terminal_state` → `_flush_history_now.commit` → `_save_body` → `os.replace`，见 [判别红测](../../artifacts/takeover-reader-red.txt)。这不是 mock 出来的 WinError。

Windows 普通 Python 文件读句柄不允许 delete-sharing，短暂读取可阻止同一 metadata 的原子 replace。此前终态只尝试一次 replace，失败会按安全契约回滚 terminal 字段，而历史已追加；stdout 任务的异常没有立即记录，外部只看到 history 已更新但 `last_result` 一直为空。这一机制能确定地产生与 MA 快照相同的状态。**原 MA 当次具体异常类型仍因日志缺失无法直接确认；本次修复的依据是上述真实内核冲突的判别复现，而非偶发重跑通过。**

## 最小实现

- Session metadata 的原子 replace 对 Windows `PermissionError.winerror` 5/32/33 最多尝试 6 次，累计退避 150 ms。重试在原有有序 persistence ticket 的 executor 内，事件循环不等待同步 sleep。
- 重试的是同一已准备的 `.json.tmp` → `.json` 原子替换，不重发模型输入、不重复 admission、不重新构造终态、不截断 canonical metadata。永久拒绝仍抛出原异常并执行原 rollback；非共享类错误立即失败。
- terminal persistence 失败立即记录完整 traceback 和 Session/Worker/generation，保留异常传播、rollback 和终态去重行为，避免持有 stdout task 时异常不可见。
- 测试 probe 可持有真实 metadata reader、记录内核冲突并在冲突发生后释放；仅测试专用启用。fake CLI 日志增加实际 stdin/stdout encoding，未改协议或超时。

## 最终验证

| 范围 | 结果 | 证据 |
|---|---|---|
| 原 125 项组合 + 3 个读锁/保存失败回归 | **128 passed，0 skipped，exit 0** | [日志](../../artifacts/takeover-revision-unit.txt)、[JUnit](../../artifacts/takeover-revision-unit.xml) |
| 原真实 5 项 + 新增真实读锁冲突 HTTP | **6 passed，0 skipped，exit 0，17.68 s** | [日志](../../artifacts/takeover-revision-http.txt)、[JUnit](../../artifacts/takeover-revision-http.xml) |
| Session incremental、persistence sharding、unread ACK、long-run persistence | **58 passed，0 skipped，exit 0** | [日志](../../artifacts/takeover-revision-store.txt)、[JUnit](../../artifacts/takeover-revision-store.xml) |
| 独立读锁判别回归 | **3 passed，exit 0** | [日志](../../artifacts/takeover-reader-green.txt) |

新增真实 HTTP 用例设置 `PYTHONUTF8=0`、`PYTHONIOENCODING=cp936:backslashreplace`，fake CLI 实测 stdin/stdout 均 `gbk`。强制真实读句柄冲突，记录 `winerror=5`；同一次终态最终 `status=done`、cursor=1、legal_offline=true，provider 仅收到一次输入，异常日志不存在。[独立实跑日志](../../artifacts/takeover-reader-http.txt)。该用例使用与 MA 失败相同的 ASCII 消息，不将结论推广为所有非 ASCII provider 协议已验证。

新增永久拒绝测试断言 6 次相同 source/target 后仍报错，主文件 bytes 不变，`last_result`/cursor 回滚、终态未 latch；普通 OSError 只尝试一次。原保存失败/unknown owner/队列安全回归保留。原 15 秒等待、断言、skip 和时限均未放宽。

真实 HTTP 批次动态端口：50818、50838、55923、61544、61564、60448。各例均核验自有实例并输出 port released；未访问 8767/8768、未使用生产 data 或停止他人进程。Provider 替身边界与原报告一致；Windows Job/进程树、HTTP、原子 metadata 和 Chromium 为真实运行。

复验命令：

```powershell
python scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_takeover_lifecycle.py tests/test_codex_takeover.py tests/test_legal_offline.py tests/test_session_worker_controls.py tests/test_queue_restart_hardening.py tests/test_steer_restart_priority.py tests/test_takeover_terminal_reader.py -q -o addopts=
python scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_takeover_lifecycle_http.py -q -s -o addopts=
python scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_session_incremental.py tests/test_persistence_sharding.py tests/test_session_unread_ack.py tests/test_long_run_persistence.py -q -o addopts=
```

本次没有 React 源码变更，没有重复 tsc/build。未运行本地全量 CI、远端 CI 或实际 provider TUI；未 push/release、未整合 main/practical。发布仍须 MA 验收及用户所述最终 GitHub CI 绿，不能由本次本地测试回执替代。
