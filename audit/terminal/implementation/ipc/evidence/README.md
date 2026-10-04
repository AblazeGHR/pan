# Pan Terminal IPC / 秘密存储证据（P1）

工作树 `terminal-ipc-implement-20261003`（branch `implement/terminal-ipc-20261003`，
起点 `718567e4`）。本目录是 **P1「传输 + 凭据」** 的可复核证据：只覆盖
`packages/core/terminal/{ipc,win_pipe,secret_store}.py` 与其测试，**不覆盖**
runner 生命周期 / service / API / 前端 / 权威仿真器（其它 TA 的范围）。

## 文件

| 文件 | 内容 |
| --- | --- |
| `generate_evidence.py` | 生成器：实跑两份测试（`-v`，逐用例结果）、采集环境、写 JSON、做遗留进程扫描 |
| `environment.json` | Python/平台/分支/HEAD/时间/临时根/网络说明（本层未开监听端口） |
| `ipc_tests.json` / `secret_store_tests.json` | 逐用例 nodeid + 结果 + 计数 + 用时 + 简短汇总 |
| `defects.json` | 实现期**先复现后修复**的 6 个缺陷（复现用例 + 修复 + 回归用例） |
| `security_boundaries.json` | 已用真实证据验证的边界 / 仅结构性验证（未实测）/ 明确不声称的部分 / 凭据分层 |
| `scope_conflicts.json` | 与其它 TA 文件的接口冲突（本 TA 未越界修改） |
| `cleanup.json` | 自建进程清理方式 + 遗留扫描 + 临时目录/网络说明 |

## 复跑

```bash
E:/software/miniforge/python.exe audit/terminal/implementation/ipc/generate_evidence.py
```

生成器只用本机命名管道与自有临时目录：不打开监听端口、不接触既有服务/凭据、
不改账户权限；其 spawn 的测试子进程由测试清理路径以**同 handle PID + raw FILETIME**
核验后终止（`win_pipe.terminate_verified_process`）。

## 隔离与安全声明（勿当作已解决）

- **同用户边界**：DPAPI 用户作用域 + owner-only DACL 只隔离其它 Windows 用户；
  同一 Windows 用户下的进程仍可解密该秘密、连接该管道。首版信任边界 = 同一 Pan 用户。
- **未实测**：其它 Windows 用户/另一安全上下文连接被拒、`PIPE_REJECT_REMOTE_CLIENTS`
  的跨主机拒绝——都需要第二账户或第二主机；本 TA **未创建账户、未改任何账户权限**，
  因此这两条只有结构性证据（DACL 枚举无其它 SID 的 allow ACE、创建参数 spy）。
- **自建临时文件 ACL 负例**（`test_foreign_ace_on_secret_file_is_rejected`）只作用于
  本 TA 在 `tmp_path` 下自建的文件，用系统 `icacls` 加宽后再断言读取被拒；
  这不是“跨用户实测拒绝”。
