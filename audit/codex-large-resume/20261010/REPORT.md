# Codex 大历史启动修复

基线 `86125d17`；工作树 `D:/project/pan-worktrees/codex-large-resume-20261010`，
分支 `fix/codex-large-resume-20261010`。未改 main/practical、实用 Session 或配置。

## 机制与修复

`ses_be105a81379e8cb1` 对应原生 thread `01a0fd4a-6b67-7163-8808-a060513799f2`。
旧 native WS 最大消息 16 MiB；真实只读 `thread/read(includeTurns=true)` 返回
28,001,059 字节 / 189 turns，触发 sent 1009，kernel poll 仍为 None。
wrapper 把 reader EOF 当作进程退出，误报 `returncode=None`。

1. native transport 接收上限提高到 **64 MiB**，仍有界。resident TUI 本地代理
   的接收限制共用常量；按需完整历史的上游 peer 也走相同 transport。
2. 所有 Pan wrapper 的已有会话恢复发送 `excludeTurns=true`。新 thread/start
   不发送该字段；不做 compact/reset/rollback，不删除/重导入历史。
3. init 事件仍仅包含 thread ID，不将历史投影回 Worker/Pan；空响应 turns 不等于
   原生模型上下文为空。live resume 元数据仍用于 TUI，TUI 按需 thread/read
   取完整历史的原路径保留。
4. native 断开时实际 poll 进程，存活内核报告 transport disconnected；1009 报
   有界接收超限，不复制 exception reason/header/token。真退出仍报告实际退出码。

当前本机 **codex-cli 0.161.0** 生成的 experimental JSON schema 中
`ThreadResumeParams.excludeTurns` 明确描述只返回 metadata/live-resume state，
不填充 thread.turns。实际 stable 初始化 capabilities={} 接受该字段；不必开启
experimentalApi。其他 Codex 版本未实际运行：未知字段被忽略的旧服务仍受 64 MiB
上限保护；显式拒绝字段的版本不承诺自动 fallback，超 64 MiB 也不保证完整读取。

官方协议参考：https://learn.chatgpt.com/docs/app-server

## 真实验证（无模型推理请求）

自建 retained Job 所有的 app-server，实际用户 thread 只读/恢复：

- 旧 16 MiB：1009，frame=28,001,059 bytes，kernel 仍存活。
- 64 MiB：同一只读历史返回 189 turns，无 RPC 错误。
- excludeTurns resume：实际 RPC 响应 **2,368 bytes**，0 turns，同一 thread ID。
- 修复后的完整 AppServer.start：6.328s；缓存 resume metadata 序列化 2,468 bytes，
  0 turns，_thread_has_turns=True，kernel alive，thread.started 恰一次。
- 随后真实 ResidentTui.start：ready=True，TUI alive，同一 kernel alive。按需历史
  加载没有被启动优化撤回。此项不是屏幕视觉验收，也不是 provider 续聊验收。
- 所有诊断 wrapper/TUI/kernel 经其 owner close 清理；未启动实用 Worker，未重启
  Pan。实用 8768 监听 PID 24196 未变。没有发送 turn/start 或修改模型上下文。

以上终端输出摘要记录在 `live-summary.json`；未保存用户完整历史/工具输出。

## 回归

使用 practical 真实 venv，通过 run_tests_isolated.py 清除 PAN_*，保护实用 data。

- 第一轮 114 pass / 1 fail：新增测试错误读取 websockets 的非公开
  ClientProtocol.max_size 属性（该版不存在），不是生产失败。改为记录真实 connect
  参数，同时保留真实 >16 MiB 消息成功接收断言。原文 `pytest.xml` 保留。
- 修复后相关 9 文件 **117 passed** / 0 skipped（7.69s）：包括真实 ConPTY +
  本地 synthetic provider 的 native/Pan round trip，不调用真实模型。
- 全部 test_codex_*.py **181 passed** / 0 skipped（9.06s），1 个既有 pydantic
  forward-reference warning。结果 `pytest-codex-all.xml`。
- 6 个新用例：大于旧上限真实 WS 接收、有界参数、新建/恢复参数分离、空 turns
  保留原生上下文标志、普通/1009 断开分类与脱敏、真实 poll 退出码。
- 既有 resumed init、native 权限/控制切换、steer、quota、worker、takeover、
  reimport/转换测试均未删除或放宽。git diff --check 通过。

未跑全库、完整实用 Pan Start UI、浏览器视觉或真实 provider 续聊；无前端/依赖
变更，无需前端构建。当前提交待合入和实用 Worker 重建，未 push/合并/服务重启。
