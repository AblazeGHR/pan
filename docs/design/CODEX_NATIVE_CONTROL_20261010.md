# Codex 同内核控制切换

## 分叉与验收目标

- 独立集成树：`D:/project/pan-worktrees/codex-native-control-integration-20261010`。
- 分支：`integrate/codex-native-control-20261010`；分叉点 main `4b166bd3`。
- 不改原 Terminal 集成树，不自动合入 main/practical，不重启实用服务。
- 切换只改变交互控制方。同一执行内核、同一 thread、当前任务继续运行。
- 原生 TUI 首先在 Pan 内展示；不以停止 Worker 后 resume 历史替代切换。

## 路线

Codex app-server 使用独立、私有的 WebSocket 连接。原生 TUI 常驻连接
这个内核，切回 Pan 不退出 TUI、不关闭连接、不发 interrupt。
官方 WebSocket/remote 能力仍属实验能力，必须进行实际 CLI 兼容验证。
来源：https://learn.chatgpt.com/docs/app-server

默认 stdio adapter 保持原状。内部 `adapter_config.native_control=true`
严格真 bool 才启用新的连接层；当前没有前端开关，不属于部署就绪能力。
loopback 端点使用随机 capability，启动参数仅含 SHA256。端点与 capability
不写入 Pan 事件、历史或浏览器。端口预选存在 bind 竞态，认证失败即拒绝，
不重试连接到未知 listener；建立后的连接丢失不自动新建/resume 内核。

## 已实现的第一批基础

- `NativeTransport`：认证 WebSocket、Unicode JSON、错误脱敏、关闭。
- `ControlOwner`：模式和 generation，检查与实际写入在同一临界区；迟到写拒绝。
- wrapper 接受独立 `control_mode` 消息，执行中也能响应，不需要结束当前 turn。
- Worker 的模式独立于 status/held 和 Session.queue_paused。
- 切换命令与实际队列 handoff 共用 queue_lock；确认丢失则保持 switching 门禁。
- durable queue 内容不删、不重排；回 Pan 唤醒 consumer，用户已有手动暂停不解除。
- Pan 控制消息在 native/switching 模式被拒绝；结果读取任务不取消。
- 模式回执在 post-terminal 过滤之前消费，idle 也能确认切换。

## 验证

实际解释器 `D:/project/Pan/.venv/Scripts/python.exe`，经
`scripts/run_tests_isolated.py` 清理继承 PAN 环境。

八文件组合：`test_delivery_semantics`、`test_codex_worker_integration`、
`test_session_worker_controls`、`test_codex_takeover`、`test_codex_control_owner`、
`test_codex_native_transport`、`test_codex_adapter`、`test_codex_native_kernel_real`。
结果：120 passed / 0 skipped，3.45s。没有修改旧断言。

真实 CLI 用例：私有 CODEX_HOME，合成凭据，本机 blackhole provider；
wrapper 与全部子进程在自有 ConPTY Job 中。确认 thread 启动和 active 状态，
四次 pan/native 模式回执，wrapper 同一 retained identity 且无 result；最后
自有 Job terminate_tree、wait_dead、close 收敛。不调用真实模型或用户 Session。
这不证明完整 TUI 接线、自然任务完成或业务报告投递。

开发中真实测试夹具更正：ConPTY 键盘 Enter 使用 CR，而不是 LF；初次 LF
输入没有触发 wrapper.readline，等待 active 超时，已改为实际 Enter 语义。

## 后续必须完成

1. 常驻原生 TUI 的私有代理与同 thread 绑定；所有写入、审批回复经过控制门。
   当前 transport capability 不允许直接交给浏览器或公开 RPC。
2. TUI 发起 turn 时，wrapper idle 仍持续消费事件；Worker 明确登记外部发起的
   turn，正确处理开始/完成、历史、汇报、忙闲，不错误归到某个 Pan 队列项。
3. 模式切换中的待审批请求转移与迟到回复拒绝，不双审批、不因换模式超时误拒。
4. Pan 内嵌终端视图、输入/resize 世代门、断连只释放控制不退出 TUI。
5. 真实 TUI 往返时当前 turn 身份持续不变；任务自然完成、队列恢复、报告接收
   与发送、浏览器刷新、退出清理的组合验证。
6. app-server/TUI 整树生命周期和异常收尾需要在真实 Worker 布局重新确认。

当前第一批只建立内部接线基础，不声称完整控制切换完成，不批准 main/practical 部署。
