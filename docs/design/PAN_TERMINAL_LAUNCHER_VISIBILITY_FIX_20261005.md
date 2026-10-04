# Terminal launcher 外部窗口修复

## 验收发现与原因

用户使用 uv 临时环境启动集成候选的 Pan，在浏览器创建终端时额外出现
Windows Terminal 窗口，标题为该环境的 `Scripts/python.exe`。
现场进程链包含 Python redirector 和真正的解释器；PTY 本身是 headless。
关闭这个外部窗口会影响 launcher，浏览器随后显示清理失败。

最小真实复现使用 `TerminalService._spawn_production_launcher` 启动当前
解释器，实际解释器通过 `GetConsoleWindow` + `IsWindowVisible` 写见证。
旧实现的 uv 路径见证为 `visible=true`；新增三项回归在修复前全部失败。
这不是需要用户保留的测试辅助窗口，而是启动参数的产品缺陷。

## 修法与边界

Windows launcher 使用 `CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB`；
breakaway 被外层 Job 拒绝时，仅去掉 breakaway，继续使用 `CREATE_NO_WINDOW`。
不再使用 `DETACHED_PROCESS`：与它同时设置时 Windows 会忽略
`CREATE_NO_WINDOW`，不能把两者叠加当作隐藏保障。
参考：[Windows process creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)。

不修改用户桌面窗口、不调用全局 ShowWindow、不关闭已有终端，
不更换解释器来绕过 shim、不改变身份权威、guard 或 Ctrl-C 初始化。
请求 breakaway 仍不等于保证 durable；外层限制仍按原机制如实报告。

## 验证与部署

定向新窗口门与既有真实 Ctrl-C 门：uv **7 passed**，直连 **7 passed**。
证据为 `audit/terminal/implementation/browser/launcher-hidden-interrupt-{uv,direct}.xml`。
Ctrl-C 门确认真实前台程序收到事件、同一 shell 可用、runner 身份未变及收尾。
扩大服务/生命周期回归为 **98 passed / 1 failed**（329.77s），失败在
Ctrl-C 发送前读取空的 ready 文件，`int('')` 抛错；不是中断失效。
该失败 XML `launcher-hidden-service-lifecycle-uv.xml` 保留不覆盖。
生产探针改为有界等待完整 PID、事件和后续 shell 见证内容；新增确定性
空/部分内容与错误内容超时检查，不删原中断、身份或收尾断言。
最终 uv 窗口/Ctrl-C/breakaway/真实 Pan 生命周期 **17 passed**（30.01s），
直连窗口/Ctrl-C **8 passed**（5.04s）；分别见
`launcher-hidden-final-uv.xml` 和 `launcher-hidden-final-direct.xml`。
先前扩大运行中的 service 原件与返工用例均通过；本次未重跑全库。

修复只在集成工作树，运行中的验收服务和旧终端不会热更新。
验收者应正常关闭自建终端后重启自己的隔离验收服务，再创建新终端检查。
本轮不重启实用 8768 或用户验收服务，不声称任意应用主动创建的窗口也被禁止。
先前 2727/10 全库结果属于修复前的冻结代码，不能作为新修复的全库结果。
