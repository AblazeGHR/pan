# CP-X5 PTY backend 候选交付

受测 SHA：40402dc797af857fc7bbb318e27e4fc27cb7e997。开发分支 feat/cp-x5-pty-20261104，基线31304d443c7c7378208690a345caa7f15afac54a；已正常hook提交、非force push到validation/x5-pty-20261104。

[CI41675759502](https://github.com/AblazeGHR/pan/actions/runs/41675759502) exact SHA completed success，三平台依赖安装、server导入、同测试清单收集/执行均成功。

| Python3.12目标 | passed | skipped | failure/error |
|---|---:|---:|---:|
| windows-latest |259|36|0|
| ubuntu-latest |245|50|0|
| macos-latest |244|51|0|

本机Windows组合259/36，35.10秒。平台skip原因完整列于r6/ci41675759502-summary.json：Windows ConPTY/Job专属、POSIX专属、Linux EIO专属、unsupported主机负控及既有sidecar依赖未安装。没有把skip当成目标证明；emulator完整snapshot/browser不在本片完成范围。

## 源码及接口

- terminal/backend_posix.py：原PtyBackend/TreeGuard适配，真实openpty、控制tty、raw输入输出、resize、三态probe、原组/已观察成员清理。创建即有界raw pump（阈值4MiB+至多64KiB单块），保留尾部、EOF/错误/取消分离，close在原证明后join再释放FD，未收敛保owner。
- terminal/pty_bootstrap_posix.py：新解释器内TIOCSCTTY/tcsetpgrp，短命socketpair强身份准入后同PID exec，不在多线程Pan里forkpty/preexec_fn。不是durable IPC或重连秘密。
- terminal/contracts.py：ProcessIdentity可选native_identity(platform/kind/birth/boot)精确tuple，不用假FILETIME；Windows原字段/无新增字段的序列化保持。
- platform/process_posix.py：spawn新增可选pass_fds默认空；原信号权限与service逻辑未改。Windows backend源未改。
- tests/test_x5_pty_backend.py、scripts/x5_pty_native_repro.py、专用.github/workflows/x5-pty.yml；文档CP_X5_PTY_20261104.md及对等矩阵更新。

目标原生测试实际覆盖controlling tty/foreground group、UTF8、echo、resize/SIGWINCH、Ctrl-C、raw ANSI小型自有TUI箭头输入、真实EOF尾部、原PtyRuntime清理、非durable detach拒绝、同组子进程清理、dead leader活成员unknown无stale信号、未知身份/权限、spawn/握手失败、强身份获取失败清理、有界背压、并发IO、即时close及创建后pump。Windows执行同接口echo/resize/runtime close和原ConPTY测试。

## macOS原红及修复证据边界

R1-R5原红及原fixture/JUnit/provenance逐轮保留在r1..r5；未删即时close断言。R5同原root/caller uid/euid/suid全501，原root随后retained wait为-15，说明TERM已生效；在此之前group SIGKILL EPERM，native组仍列出原root，不可按空组或权限不等处理。

Windows backend有创建即pump，POSIX原按需read，留下canonical echo无人排空。Apple XNU [ttywait](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/tty.c) 注明proc_exit/vproc_exit等待输出；此为修复机制依据。R6补原始字节持续pump后，原即时close与原owner负控在Linux/macOS全部通过，未加sleep/skip，不扩信号权限。

独立无Pan/pytest程序八组对照（immediate/ready、输入有无、drain有无）本轮均原root退出，drain组实际读出echo。它未稳定复现同EPERM，因此不把它当成完整内核因果证明或额外产品验收；无drain退出竞争与原进程事实可供复查。

## 未完成产品入口与最小下一片

terminal.native和terminal.detach在POSIX继续false；service.lifecycle已签收声明不变，hiddenDesktop=false。此为可复用底层候选，非POSIX Terminal功能完成，非X5全完成。

现有TerminalService._start在启动前依赖DPAPI SecretStore，后续complete_bootstrap/RunnerClient使用Windows端点身份握手；TerminalRunner._bootstrap/_prepare固定SecretStore/win_pipe.PipeServer；HeadlessEmulator另有Windows Job宿主；web/terminal_api.build_runtime有非Windowsgate。只换ConPTY工厂不能接通REST/WS，不可绕过认证、终端Session绑定或伪造durable重连。

最小下一片建议：从原TerminalService抽取非durable本机host装配接口，Windows原RunnerClient不变；POSIX服务进程持有本片PtyRuntime，继续使用同一TerminalRegistry/AttachmentRegistry/OutputLog，定义服务崩溃残留/reconcile unknown及连接断线/重连。原REST/WS授权和Session绑定必须走真实目标链路；现有API测试只是共享契约回归，不冒充POSIXHTTP/WS。durable独立runner/IPC/secretStore及完整emulator宿主分别后续裁决。Rewind/provider菜单未触碰。

路径所有权：当前TA持有新backend/bootstrap、contracts可选身份、process_posix可选FD、专用测试/workflow/证据；下一片service.py+薄host adapter+terminal_api gate；registry/attachments/output只复用、必要最小变更串行；emulator、durable IPC/secretStore独立分项。共享平台注册表由MA串行协调，正式cap须入口与目标证据齐全再开放。

## 工件与边界

r6/ci41675759502.zip是原run.json、三平台JUnit/target.json及两平台native-repro.json；同目录逐文件SHA256 manifest、zip SHA256与汇总已校验。原散文件也保留。R6唯一waiter job_40ea7ce4171395fd96a698e3终态completed/exit0；不重复watcher。

main仍c9dc621a01ccb568878c8a37297c034cbaec18de，practical仍4af89c694e37fd0d2c1e378e9ad51e5c9edaafdd；service验证ref仍dc87428e，原service交付分支仍31304d44。无部署/真实provider账号/profile/8767/8768/系统服务注册，无React源码改动和本轮浏览器验收。后续证据提交仅文档/工件，不改变受测源码。
