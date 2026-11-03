# X5 正式 service.lifecycle 声明候选

本片在已验底层 e6749bde 上声明 Windows/Linux/macOS service.lifecycle 支持，并将 HTTP restart/exit status.available 改用同一 capability。React 已检查 capability 与 available，无前端源码变更。

范围：同用户 direct Python（system/venv），启动终端及合成 Worker 结束后继续。无 logout/reboot 自启，无任意逃逸后代 containment；POSIX 异 PID interpreter handoff unsupported；weak debug 不增杀权限；hiddenDesktop 在 POSIX 保持 false。

原 receipt、授权、取消 fence、启动锁、unknown 只查不重放及原树残留证明链不变。native harness 已删除能力源码注入，真实读取 /api/platform、restart/exit status；原 detached supervisor 契约随 POSIX 正式声明执行，独立 false 负控保留。

本机 Windows 契约：118 passed、5 skipped，exit 0。入口 release/windows-contracts.xml；命令使用 scripts/run_tests_isolated.py 包装 pytest，覆盖 platform_layer/platform_api/service_process_backend/x5_service_evidence/x5_service_network/launcher/lifecycle_fast_contract/lifecycle_prelaunch_fence/lifecycle_strict_successors/p5b_lifecycle_action。无真实用户数据/账号/provider，无 8767/8768。

正式声明受测/可发布候选：`dc87428e66043b22a00ab72a9de3ae03d0ef73ed`。正常 hook 提交后非 force push 到 validation/x5-service-20261103。独立 [CI 41668990719](https://github.com/AblazeGHR/pan/actions/runs/41668990719) exact SHA completed success。

| 目标 / Python 3.12 | 契约 passed/skipped | 无注入真实 main/CLI/HTTP passed/skipped |
|---|---|---|
| windows-latest | 118 / 5 | 1 / 0 |
| ubuntu-latest | 121 / 2 | 1 / 0 |
| macos-latest | 121 / 2 | 1 / 0 |

三平台 native JSON 均确认精确 sourceSha、trialCapabilityInjection=false、declaredCapabilityUnchanged=true、HTTP capability/status 可用、启动 CLI 及合成 Worker 已退出而服务存活、原 request 查询无重放、CLI restart/exit/status、真实 CLI/canonical 锁竞争 unknown 且无重放、最终端口释放。两个真实 detached supervisor 契约三平台均执行通过。POSIX 两个 skip 仅是无真实 unsupported host；独立注入 false 负控仍执行。Windows 另外三个 skip 是 POSIX 原组/残留/强身份清理目标测试，已在 Linux/macOS 执行。

契约覆盖授权、取消停止前后、原 unknown 只查、原树残留、stale birth、权限/启动失败/超时负控；以 JUnit 原测试身份为准，不将全部负控称为真实 HTTP。native Worker 为自有 OS 进程替身，不是第三方 provider。

完整原工件：release/ci41668990719.zip；zip SHA-256、逐文件 manifest、JUnit/native 汇总在同目录。归档包含 run.json、六个原始 JUnit、三平台原 native JSON/命令记录。Windows 本机 native 另见 release/windows-native.xml 与 windows-native/native-service.json（1 passed，35.20 秒）。

唯一 waiter Job job_5ee4e7ad9365953f6ad089d8 已 completed/exit 0、notification delivered、snapshot cleaned、processTreeEnded=true。无重复 dispatch/watcher。

文件/API：platform/__init__.py 注册范围；server.py 的 /api/main/{restart,exit}/status.available 与 unsupported 文案；main_lifecycle.py 说明文案；native harness/平台契约/独立 workflow；README、CLI、中英文手册与 X5 矩阵。无新 ledger/schema，无前端源码改动；复用既有 UI capability+available gate，未声称本轮真实浏览器验收。

main 仍 c9dc621a01ccb568878c8a37297c034cbaec18de；practical 仍 4af89c694e37fd0d2c1e378e9ad51e5c9edaafdd。无部署、真实 profile/账号测试、系统服务注册。由 MA 决定整合与发布。本报告后续证据提交不改变受测源码。

后续拆分/所有权：安装/logout/boot 分项持有新增 systemd/launchd installer、安装文档及 harness；PTY/detach/Rewind/secretStore 各持有独立 backend/契约与能力文档，产品安全差异由 MA 裁决。本片不实现其它项。注册表/server.py 为共享集成路径，由 MA 串行协调。
