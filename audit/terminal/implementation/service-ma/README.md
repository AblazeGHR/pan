# MA 差量验收与小修（2026-10-03）

对象为固定 `4f5241ce276fe64efb7729b91fc8c9a1da1fd0d7`；新隔离树
`terminal-service-ma-20261003`。已无冲突导入已接受卫生提交 `7f3ed5ce`
（本树 `2215558f`）。原实现、卫生、审查树未修改；旧证据不刷新。

## MA 发现与修复

- 重发锁只能互斥，不能让锁外读取的失败结果变新：锁内先 `call.run(0)` 重读，
  另一 caller 已成功时不 reset；在途保 owner、明确返回 closing。
- 精确身份不接受 `int(float)` 截断或 bool→int。允许真正 int 与 ASCII 十进制字符串，
  其它类型为静态 unknown；真实整数正控与原错 PID/缺 PID 门不变。
- 删除 attach helper 在 `return True` 后的不可达残留，不改变行为。
- 重连测试此前统计本进程所有终端的心跳线程：当旧测试先跑时产生顺序依赖。
  改为统计当前 terminal 的准确线程名；同终端重复线程仍必被发现。

## 实际测试结果

以下为本轮终端工具输出的摘要，不声称保存了原始日志文件。

1. 新 MA 四门在修复前实际 `4 failed`：成功被再次 reset、浮点 PID、浮点 FILETIME、bool PID。
2. 修复后第一轮两套组合 `80 passed / 1 failed / 6 deselected`；失败为全局心跳计数
   （4 个不同终端线程）导致的顺序依赖，非本终端重连泄漏。修改计数范围后继续验证。
3. 修后直连三文件纯逻辑 `81 passed / 6 deselected`（11.14s）。
4. 同组合 uv 隔离 `81 passed / 6 deselected`（11.30s）；未重跑六项真实层。

复跑命令（直连；uv 可采用原 r3 隔离依赖组合）：

```powershell
E:/software/miniforge/python.exe -X utf8 -m pytest tests/test_terminal_service_ma.py tests/test_terminal_service.py tests/test_terminal_service_rework.py -o addopts= -q -p no:cacheprovider -k 'not real'
```

原 TA 的 r3 源锚定 4/4 已对原提交独立复算一致；TA 的 77×2、两项真实恢复成功
仅记为核对过入库日志的 TA 执行证据，MA本轮未重跑真实层。

## 边界

只接受基础服务的限定子集，不批准 Web/MCP 接线。容量仅单实例；调用方预算非 OS 硬 SLA；
真实 detach、Ctrl-C、浏览器/鉴权、跨用户/主机、长稳/POSIX 未验收。
测试均为自有假进程，不启动 ConPTY/sidecar/网络监听，不涉及真实 PID 清理。
本树仅隔离验收/整合，不合入 main/practical，不 push/build/restart。
