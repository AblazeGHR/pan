# X5 正式 service.lifecycle 声明候选

本片在已验底层 e6749bde 上声明 Windows/Linux/macOS service.lifecycle 支持，并将 HTTP restart/exit status.available 改用同一 capability。React 已检查 capability 与 available，无前端源码变更。

范围：同用户 direct Python（system/venv），启动终端及合成 Worker 结束后继续。无 logout/reboot 自启，无任意逃逸后代 containment；POSIX 异 PID interpreter handoff unsupported；weak debug 不增杀权限；hiddenDesktop 在 POSIX 保持 false。

原 receipt、授权、取消 fence、启动锁、unknown 只查不重放及原树残留证明链不变。native harness 已删除能力源码注入，真实读取 /api/platform、restart/exit status；原 detached supervisor 契约随 POSIX 正式声明执行，独立 false 负控保留。

本机 Windows 契约：118 passed、5 skipped，exit 0。入口 release/windows-contracts.xml；命令使用 scripts/run_tests_isolated.py 包装 pytest，覆盖 platform_layer/platform_api/service_process_backend/x5_service_evidence/x5_service_network/launcher/lifecycle_fast_contract/lifecycle_prelaunch_fence/lifecycle_strict_successors/p5b_lifecycle_action。无真实用户数据/账号/provider，无 8767/8768。

无注入 native 与三平台远端证据尚待此提交验证；本文件随后补精确受测 SHA 与 CI。底层历史报告 FINAL_REPORT.md 不代表本片声明门禁。
