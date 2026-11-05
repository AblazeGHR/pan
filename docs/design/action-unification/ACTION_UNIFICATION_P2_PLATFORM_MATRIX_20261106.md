# P2 命令执行能力矩阵

沿用 `packages/core/platform` 的 `processOwner` capability 与既有平台实现，不添加平台分支到业务模块。

| 项目 | Windows | Linux | macOS | 本片证据 |
|---|---|---|---|---|
| argv/环境/cwd/hideWindow | 既有 WindowsProcessOwner | 既有 PosixProcessOwner | 既有 PosixProcessOwner | Windows 隔离子进程；POSIX 实机未验证 |
| shell 默认解释器 | 原 COMSPEC `/d /s /c` | 原 `/bin/sh -c` | 原 `/bin/sh -c` | Windows 真实 shell；POSIX 实机未验证 |
| 外部脚本/显式解释器 | 当前文件 + 观测摘要 | 同一共享 descriptor | 同一共享 descriptor | Windows Python 脚本两次内容变化 |
| owner/超时/取消/树清理 | Job Object/精确进程身份 | 原进程组/精确身份 | 原平台身份/进程组实现 | Windows owner 失效、timeout 与取消负控 |
| 不支持/未知能力 | API 明确 capability_unsupported | 同左 | 同左 | 缺失 capability 在 intent 前拒绝 |
| 三平台导入/收集/CI | 本地导入与聚焦测试 | 未运行 | 未运行 | 不将 Windows 结果计为三平台验收 |

direct 与 Job adapter 共用结果与清理规则。未知/不完整 cleanup proof 不能转成成功或重试许可；原 IndependentExecutor 的 `process` 路径继续保留。

审查补证：新增真实 TCP 测试复用跨平台 ProcessOwner、Python socket 与 httpx；没有新增 Windows-only 顶层导入、盘符、`/proc` 或业务层 OS 分支。Windows/Python 3.14 上已验证 socket 断开、HTTP client task 取消、完整 PanServer graceful drain 与 shutdown deadline 取消后原回执冷查询；Linux/macOS 实机与远端 CI 仍未验证，不把静态边界检查计为三平台运行通过。
