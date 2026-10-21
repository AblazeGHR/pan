# Event Trigger core 并发验收返修候选

工作树 `D:/project/pan-worktrees/event-trigger-core-20261022`，分支
`feature/event-trigger-core-20261022`；返修基线 `b295773d325c0b543f865953ad9ccf2aaf43e4b4`。
本报告与返修代码同提交，精确候选 SHA 由 `git log -1 --format=%H` 核验。

## 修前证据

在基线实现上新增可控 asyncio.Event 屏障，不依赖随机时序。首次四个用例全部失败，
命令退出码 1，见 [before.log](before.log) 和 [before.xml](before.xml)：

- `test_empty_snapshot_then_match_cannot_lose_wakeup`：runner 已读到空快照但尚未
  返回，第二个 Event 完成持久化和 kick；释放旧快照后，新 queued 未被执行，
  有界完成等待超时。没有额外事件或手动再次 kick 掩盖滞留。
- `test_disable_between_claim_and_dependency_deferral_cancels`：冻结成功 claim
  返回，撤销依赖资格，用户 disable，随后释放 runner；旧实现在关闭 Trigger 下
  写回 queued，预期 cancelled 失败。
- `test_kick_read_inflight_at_close_must_not_spawn`：冻结 kick 的已读取快照，
  close 完成后再释放；旧实现创建了关闭后的 runner task。
- `test_start_recovery_inflight_at_close_must_not_reopen`：冻结 start 恢复返回，
  先 close 后释放；旧实现将 closed 改回 false。

修前运行时测试文件包含上述四例；后续增加四例边界覆盖，没有修改这四例的断言。
可在基线应用测试文件后按上述四个 node ID 定向重现。

## 修复保证

每个 Trigger 一个 `_runner` owner 和一个合并 wake 标记。kick 对活 owner 置 wake，
空快照返回后 owner 检查 wake 并重新读取。wake 检查和 owner 注销之间没有 await，
因此后来的 kick 只会唤醒现有 owner 或创建继任者。无轮询、定时器或长生命周期锁。
相同 Trigger 不会并行执行，其他 Trigger 的 runner 互相独立。

kick 在磁盘读取之后复检 close latch，close 同步关闭准入并取消/等待固定 task
快照，随后清理登记，包括 coroutine 尚未执行第一步就被取消的 task。start 的恢复
即使晚于 close 返回，也不会重新开放本实例。没有修改 HTTP API 或前端契约。

依赖不可用的 pre-effect 退回通过 `Ledger.defer_unavailable` 一次原子事务完成：
必须仍是该 revision 的 running Run；事务内复检当前 Trigger。启用则 queued，
关闭/删除则 cancelled；状态或 revision 已变化则原样保留。disable 在退回之前或
之后都收敛到 cancelled，旧退回请求也不能覆盖已取消 Run。

新增八个确定性回归覆盖：四个修前失败交错、八路同时 kick 的单 runner 保证、
退回/disable 两种顺序及 cancel 后 stale deferral、owner 退休后的继任启动、
runner 未执行第一步就 close 的登记清理。

## 最终验证

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_automation_concurrency.py tests/test_automation_core.py tests/test_automation_http.py tests/test_action_groups.py tests/test_action_ids.py tests/test_session_queue_api.py -o addopts= -q --junitxml=artifacts/event-trigger-rework/final.xml
python -m compileall -q packages/automation tests/test_automation_concurrency.py
git diff --cached --check
```

最终组合：**120 passed，0 failed，0 errors，0 skipped，退出码 0**；compileall
退出码 0。最终 JUnit 见 [final.xml](final.xml)，日志见 [final.log](final.log)，
退出码见 [exit-codes.json](exit-codes.json)。5 条现有 timeout 配置/标记与 httpx
弃用 warnings 保留，没有 skip/xfail、弱化断言或全仓测试。

本轮重新运行真实隔离 HTTP/fakeCLI 闭环：端口 **57156**，PID **43200**；
[final-http-evidence.json](final-http-evidence.json) 记录 Event/Run/暂停队列结果。
`portReleased=true`、`ownedProcessesRemaining=[]`、`sentinelUnchanged=true`。
没有调用收费 provider、访问 8767/8768、真实 data 或现有服务。

开始返修时工作树已有两份 HTTP 证据改动；原 bytes 已保存在
`ma-input-http-evidence.json` 和 `ma-input-http-server.log`，未丢弃验收输入。
本轮 HTTP 新证据单独存于本目录，原 `event-trigger-core` 路径恢复为基线内容，
使其历史报告与历史证据对应。文本测试日志和失败 JUnit 的堆栈文本仅统一换行和去除行尾空格。

## 范围与验收边界

[核心 API 契约](../../docs/design/Event-Trigger-Action-v0.1-core-contract.md)
保持原有 HTTP/data shapes，仅补充内部调度与原子退回保证，供前端继续并行。
未展开前端、外部插件传输、query 或其他阶段；原有长期账本/可靠 Event 投递/
真人验收边界不变。并发结论来自确定性屏障回归，不声称穷尽所有 OS 调度或断电故障。

未移动 main/practical、push/release、写共享依赖、派子任务或操作现有服务。
完成 clean 候选后停止，等待 MA 独立验收。
