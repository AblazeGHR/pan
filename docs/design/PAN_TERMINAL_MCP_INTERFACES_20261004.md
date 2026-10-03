# Terminal MCP 接线（MA 实施中）

## 当前已实现层

`packages/web/terminal_tools.py` 是受宿主调用的同步 dispatch 核心，使用同一
TerminalService，不新建服务实例。宿主必须在 TerminalRuntime.call 的可追踪
线程内调用，并提供可信 caller resolver 与既有能力检查。它不是公开 HTTP
鉴权器，不接受任意 HTTP 头作为已验证身份。

身份缺失或解析失败拒绝；created_by 取核验后的 caller，不取目标 session_id。
session_id/workspace_id 是上下文元数据，关联 session 仍须通过既有能力检查。
只读 caller 只可 list/get/read/snapshot。未知命令和字段拒绝，公共投影复用
REST 的尺寸、游标、快照与脱敏规则。控制代、IPC token 不返回。

input 必须显式 `take_control=true`，代表接管当前输入控制权：取得实际 core
control lease，执行输入，再释放。此前浏览器控制权可能因此被撤销，调用者
不能把它当成无副作用观察。未显式同意时零 attach/写。异常或取消后同步
worker 仍负责 finally；释放失败保留真实 token，宿主在停止前调用
retry_releases 并保留未收敛 dispatcher。最多保留 128 token，满时拒绝新接管。
dispatch 非阻塞串行准入；忙时不建立第二等待队列。预算不是 OS 硬 SLA。

## 验证与待完成

纯逻辑门控 25 项通过（本机 miniforge，2026-10-04）。这些只证明 dispatcher
规则，不证明真实 MCP、HTTP、本机进程入口或生产生命周期。

仍需实现：受信本机 MCP 传输与 caller resolver、能力映射、stdio 工具注册、
同一 app runtime 接线、停止时未释放 token 的真实消费、隔离真实链验证。
当前不注册路由，不向现有运行服务提供这些工具。WS 在途实现未读取或修改。
真实浏览器、Ctrl-C、durable detach、跨用户/主机与长稳仍未验收。
