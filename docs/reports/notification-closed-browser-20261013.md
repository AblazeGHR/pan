# 浏览器关闭后的 system 通知与 browser 失败回退调查

> 历史调查/阶段记录，描述当时的基线与验证范围，不是当前操作指南。当前已合入版本的通知选项与 60 秒点击窗口限制见[用户手册](../USER_MANUAL.md#integrations)；真实 Windows OS 点击仍未验证。

基线：`c981dbe533e96612dc7170cc8d968c8a8c9b8a73`；独立工作树 `notification-closed-sol-20261013`。状态：原生候选待验证/MA 验收，禁止快进。未改变 main/practical、push、重启现存服务或请求 8767/8768 测试；未写真实用户配置、凭证或 Session。

## 结论与实现范围

1. NotifyIcon 的 `BalloonTipClicked` 可以接到现有 `?session` 深链。候选实现补 WinForms 消息循环、STA、单次点击守卫与 finally 释放，每条完成通知使用独立 helper/不可变 Session 目标；最多 60 秒后退出。默认浏览器可在关闭时由 Shell URL 激活启动，这是代码路径推断，**没有真实原生点击证据**。无法保证 60 秒后的通知中心操作，不能称为可靠持久激活方案。
2. **放弃 browser 失败才发 system 的设置**。browser API 可报告部分确定错误，但无法可靠确认 OS 实际展示/投递失败。保持现有 browser/system 开关、双通道默认行为、无 ACK/重试新架构。

## 现行约束与代码

当前 worktree、主开发树与 Git 跟踪内容均未找到 AGENTS.md；worktree 没有 .workflow。读取 `D:/project/Pan-main/.workflow/constraints.md` 与 overview。任务明确允许独立 frozen 安装，且禁止安装写穿 junction；该任务约束优先于 workflow 的 junction 准备流程。

先阅读阶段一报告与 `notifications.py`、`useWebSocket.ts`、session-deeplink helper 和对应测试。原系统发送器只是 6 秒 sleep，没有 click handler；browser 通知依赖活页面的 WS，无客户端 ACK，多 tab 各自发送。

候选仅修改 `packages/core/notifications.py`：

- 完成通知通过 `resolve_pan_api_url()` 定位当前本机实例，生成 `/react/?session=<opaque id>`。仅接受 http/https loopback、合法 ID，无 userinfo、query、fragment 或任意路径；异常禁用点击、保留普通系统通知。
- 不采纳浏览器请求 Host/Origin；不携带标题、正文、凭证到 URL。不新增用户配置或协议/AUMID 注册。此策略仅服务本机原生通知，不支持 tunnel 路径前缀。
- 保留两参数可注入 sender 接口；提醒/MCP 手工发送没有新增深链。system 开关关闭时不创建 helper。完成通知仍在后台线程中发送。
- 带点击目标的 subprocess 上限 75 秒、message loop 上限 60 秒；成功退出仅报告 `delivery=unknown`。WS 中现有 `method=background` 表示排队派发，不是投递成功。

代价/缺口：每条通知延长 helper/线程占用，密集完成时可能受线程池容量影响；非持久窗口内成功不意味着通知中心长期可点击；Windows Do Not Disturb/锁屏/禁用通知、默认浏览器激活与多通知归属尚未实测。原有正文可能上锁屏的隐私行为保持不变。

## Browser 可靠性与 ACK 边界

| 信号 | 能确认 | 不能确认 |
|---|---|---|
| unsupported / permission default 或 denied | 当前客户端不能按现路径发送 | 全部 tab、客户端或 OS 的结果 |
| 构造抛异常 / error event | 对应客户端的部分 API 失败 | 所有平台展示失败均会报告 error |
| 构造返回 / show event | API 对象创建 / UA 执行显示步骤 | 可见 banner、通知中心展示、用户收到或看见 |
| SW showNotification resolve | UA 完成该调用的规定步骤 | OS 实际展示、浏览器完全退出后的执行 |
| HTTP/WS ACK | ACK 消息已到服务端 | ACK 前后进程崩溃与 OS 展示；消息丢失属于未知 |
| 无客户端、浏览器关闭、超时 | 当前无可确认结果 | 可靠失败；把未知当失败会造成重复投递 |

多个 tab 的失败与成功可同时出现，不能“一个 tab 报失败就 system”。实现准确聚合至少要 event 身份、客户端归属/选主、去重、每次结果与 ACK 期限；仍无法消除 OS 展示未知。未新增这些字段或持久账本，也未静默修改 Notification 页面。

规范依据：WHATWG [show steps](https://notifications.spec.whatwg.org/#showing-a-notification) 的显示步骤明确可只是调用平台 API，之后排队 show event；[constructor](https://notifications.spec.whatwg.org/#dom-notification-notification) 异步权限检查不等同于构造返回。Microsoft [BalloonTipClicked](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon.balloontipclicked) 证明事件 API 存在；均不能替代本机实际点击验收。

## 替代方向

- WinRT toast + protocol URL 可以脱离 helper 激活，但规范化桌面通知身份/安装注册是额外产品工作；不得冒用既有应用身份或修改全局配置作为实验捷径。未实现。
- SW 持久通知的 notificationclick/openWindow 可在没有活页面时处理已展示通知；但现有 worker.result 只通过活客户端 WS 送达，SW 不提供关闭浏览器时接收完成事件的通道。Web Push 引入订阅、凭证、推送服务、浏览器后台限制，超出“不扩大架构”；不把注册一个 SW 当关闭浏览器投递已完成。

## 验证事实

- Python 定向测试：33 passed（新 21 项 + 原 12 项）；先观察到目标快照回归失败，再修复闭包捕获后通过。pytest 存在 timeout 配置与 Pydantic forward-ref 两类环境 warning，未压制。
- `audit/notification-closed/native-simulate.py`：实际运行 Windows PowerShell + WinForms message loop，以反射调用 **managed** `OnBalloonTipClicked()`，不显示通知、不启动浏览器。双事件单次派发、模拟启动失败退出码两项通过；JSON 明确 `nativeActualClick=false`、`osNotificationShown=false`。初次实验错误传 EventArgs 导致计时器停止后超时，已改为该方法的真实无参数签名；这不是 OS 点击。
- 真实原生实验：准备 `native-probe.ps1`，只允许独立 Chrome profile；启动命令被自动审批以 `blocked by policy` 拒绝。未执行原生显示、点击、启动和 Session 冷导航闭环。脚本保留，不绕过拒绝。
- 验证 Job `job_c059bfceae5e5d61728bd686` completed/exitCode=0，日志含 `NOTIFICATION_CLOSED_VALIDATION_COMPLETE`，终态通知已实际返回本 TA。前端定向 113/113 passed；tsc/build/66 压缩资产通过；Python 再验 33 passed；diff check 通过。
- 隔离真实 backend/browser deeplink E2E 10/10：实例 PID 35452、动态端口 53905、本 worktree 独立 runtime；Chromium 153.0.8010.12，0 外部请求、0 pageerror。包含 worker 完成→handler→聊天路由/Session、删除、失败恢复、用户抢占、连击、双 tab。仍以 Notification stub/程序化 handler 验证，**不属于原生点击**；headless visibilityState=visible，不证明真实后台窗口聚焦。系统开关全部关闭，未发系统通知。
- 首个验证 Job `job_03f1fab51be7de5129a84bc7`：frozen install 成功（466 包、4m57.6s、独立 store/copy）；进入 Vitest 后，PowerShell 5 的 Stop 把测试预期 stderr 当异常终止，未产生测试终态。修复验证包装器为保留 stderr、按真实 exitCode 判定；测试断言/源码未改变。其余步骤当时尚未运行。

MA 验收前不得快进。失败探索与产物保留在该工作树；需对原生点击缺口、60 秒语义和资源占用风险决定是否接受候选。
