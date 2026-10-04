# Terminal MCP 接线（MA 隔离实现）

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

## 本机传输与生命周期

FastMCP 注册 terminal_create/list/get/read/snapshot/input/close/detach 八个工具。
仅 stdio 入口启用；SSE/streamable-http 拒绝使用此进程 Session 身份。
PAN_API_URL 必须是无 userinfo/path/query 的本机 HTTP 字面回环地址；专用代理不跟随重定向。
caller 来自 PAN_AGENT_SESSION_ID，缺失或 Session 不可解析时拒绝，不沿用通用工具
的“无身份不受限”兼容行为。默认 create 关联调用方 Session，显式关联受既有 managed 能力约束。

POST /api/terminal-tools/{operation} 是专用本机进程桥：非回环 peer、有 Origin
或任意 Sec-Fetch 头拒绝。X-Pan-Terminal-Caller 仍是同用户本机进程的身份断言，
由 Session 存储重新解析，**不是加密身份凭据，也不是远程鉴权**；不信任代理转发头。
读取请求最多 256KiB，跨调用共用既有 runtime 四槽，取消不提前回收底层在途槽。
restricted caller 的 list 过滤关联 Session；无 Session 关联的终端不给受限 caller 操作。
create/get/read/input 等操作通过相同 dispatcher，不接受客户端传入 trusted_local 或 token。
Session readonly_session 是对外来管理操作的保护，不在这里擅自解释为禁止该 Session 发出工具；
dispatcher 的 readonly 测试注入能力不是新增 Session schema。

停止顺序：WS 连接 → MCP host → REST service。MCP 先关准入，等待已接纳 dispatch
worker，再消费实际 token 的 release；共享 2 秒等待预算，未收敛保留 host、worker、token
引用和报告，不假成功、不取消同步 worker。预算不是 OS 硬 SLA。注册为 server 薄接入，
未启动或重启用户正在运行的 Pan。

验证：核心 25 门控；传输 10 项（包含真实 stdio ClientSession→隔离 uvicorn HTTP→
真实 TerminalService/ConPTY/pipe/headless 的 create、显式输入、read、snapshot、close）。
真实链的 caller 存储由测试隔离注入，不代表完整 Pan 部署或真实用户权限验收。
真实终端收尾检查 retained identity 死亡与秘密删除；快照保持 partial、不假 full。
另有取消在途 input 的 owner 保留/迟到 finally 回收、Origin/Fetch/远端拒绝与 scope 门。
真实浏览器、Ctrl-C、durable detach、跨用户/主机、长稳仍未验收。
