# Windows Job 隐藏窗口试用

`hideWindow=true`（默认）在 Windows 上为命令创建独立、不可见的 `PanJob_*` desktop。正常继承该 desktop 的子孙 GUI/控制台留在该 desktop；Pan 不切换用户当前的 input desktop。

启动使用当前用户的派生 token，DefaultDACL 为该用户增加 `PROCESS_SET_INFORMATION`（0x200）的拒绝项。本机实测这能抑制普通新控制台／AllocConsole 的默认 Terminal 委托路径。它不是 Windows 提供的所有 broker 隔离保证。没有更改用户／系统的全局 ACL、Terminal 配置或登录身份。

对主动选择用户 input desktop 的 owned 窗口，外部 WinEvent guard 会尽力请求缩小、设置不激活样式和隐藏。它只操作通过 retained process handle 与创建身份验证的 owned 窗口。通知和隐藏请求是异步的：**仍可能短暂闪现、出现完整窗口或抢焦点，也可能被程序重新显示**。共享用户 Terminal、外部 broker 和他人窗口不在处理范围内。

试用边界：

- `hideWindow=false` 沿用原可见模式；非 Windows 沿用原执行方式。
- Windows hidden 启动失败会明确报错，不静默改走可见路径。
- 按 PID 打开 hidden 进程的 `PROCESS_SET_INFORMATION` 或含该位的更大权限（如 `PROCESS_ALL_ACCESS`）可能被拒绝；外部优先级／亲和性管理、调试／诊断工具等需验证。DefaultDACL 也用于其他新建内核对象，不能把本批文件、event、mutex、section、pipe 测试推广为全部兼容。
- GUI 的交互环境变成私有 desktop，需要人机交互的程序可能等待不可见对话框。cwd、日志、退出码和本批文件／IPC 访问已验证。
- 独立 Job service 的 runner 崩溃由内核 Job Object 清理整树，恢复记 UNKNOWN 并拒绝重复执行同一已领取 Job。legacy 保留原较弱生命周期：runner 崩溃可能留下任务／子进程，registry 记 failed；试用不把它提升为同等 crash containment。
- 两条普通 argv 接口均没有原生任务 timeout 字段；需要外部期限策略调用取消接口。controller 的 recovery-loop shutdown 不取消持久 Job。

没有默认 debugger、注入 DLL、alpha 透明化、屏幕外定位或新工具链要求。这些新版研究仍保留在 `research/job-descendant-hidden-20261013` 的 `08589480847130e8e04ecbb3341c06595ca614ae`，等待后续选择。

建议先用于无需可见交互的自有任务；该设置是实用窗口策略，不是强制不可见的安全沙箱。
