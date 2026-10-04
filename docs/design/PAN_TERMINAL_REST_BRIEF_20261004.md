# P2 REST/lifespan 第一批 — MA 已决执行任务

基线 3dafe3f8cbcca9c05979beb831d2c45e6d5c809c；任务 T-TERMINAL-PTY-20261003/rest/impl/1。
只在本注册 worktree implement/terminal-api-20261004 实施。目标是把已接受 TerminalService 接到 Pan，不是重新设计核心。先读 CODEBUDDY.md、本任务书；只按需读 service 公共方法、对应接口文档、计划 §6.3 和 server lifespan。不要重读全部历史审查。

## 范围

可写：新增 packages/web/terminal_api.py、tests/test_terminal_api.py、docs/design/PAN_TERMINAL_REST_INTERFACES_20261004.md、audit/terminal/implementation/rest/**；packages/web/server.py 只允许路由注册、lifespan 起停接入和必要 import。本任务书不可修改。核心 service/launcher/runner/IPC/emulator/registry、旧测试/证据、MCP、前端、总依赖/锁、.workflow 均只读。
不做 WS、MCP、浏览器输入/resize、生产部署、全库测试或新架构。遇到本任务方案与既有 API 确定不相容，报具体矛盾，不擅自扩大写范围。

## REST 已决接口

APIRouter prefix /api/terminals；成功 envelope {ok:true,result:...}；失败 {ok:false,error:{code:<静态分类>}}，不返回异常文本/路径/秘密/pipe。不把内部 service.describe 当公网接口。

- GET /api/terminals → service.list。
- GET /api/terminals/{id} → service.get。
- POST /api/terminals → service.create；JSON 仅 rows(默认24)、cols(80)、cwd(可空)、workspace_id/session_id(仅元数据)。拒绝额外字段，尤其 context/created_by/trusted_local/terminal_id/token/shell_argv。rows/cols 真整数（非 bool）1..1000；字符串最大4096；context 固定 ServiceContext(created_by="web-local", trusted_local=True)，来自通过入口检查的本地信任面，不伪称登录认证或 workspace 授权。
- GET /api/terminals/{id}/read：cursor 为 ASCII 非负十进制字符串，缺省 "0"，范围 uint64；max_bytes 真整数1..131072默认65536；service.read。bytes 转 data_b64，seq/next_cursor/total_bytes/first_retained_seq/gap 两端输出十进制字符串，size 保持普通小整数。不补零、不跳缺口、不重置引擎。
- POST /api/terminals/{id}/snapshot：JSON 仅 timeout_ms 真整数1..5000默认5000；service.snapshot。serialized_screen 转 UTF8原字节的 data_b64（来源 bytes 则直接编码）；128KiB 上限，不截断伪造状态，超界返回静态 snapshot-too-large；cursor 字符串或 null；保留 fidelity/recovery/note/F5 bool-or-null/continuation_hint/applied_evicted，不因 reasons 空升级。诊断仅白名单公共事实，不透出未知键。
- POST /api/terminals/{id}/close：空 JSON，固定 reason explicit-close；service.close。CleanupUnconfirmed → HTTP409 cleanup-unconfirmed，不宣称 exited，不删凭据；重试正常可达。
- POST /api/terminals/{id}/detach：空 JSON；service.detach。拒绝 →409 detach-refused，不伪称 durable。

其他映射：非法输入422（静态错误，不回显原始 input，禁用该 router 的默认含 input 验证内容）；未知合法 id404；CapacityExceeded429；not-attached409；closing/unavailable503；startup失败502；未知异常500静态 internal-error。validate_terminal_id 必须在调用 service 前完成。公网返回使用明确字段投影（list/get 沿 service 公共视图字段，禁止未知新增字段自动外泄）；snapshot engine 只允许公共标识，不泛化复制任意 Mapping。

## 入口安全（必须先于服务调用/实例创建）

本批所有 REST（含 GET）要求 Origin 精确 allowlist。默认 http://127.0.0.1:<PAN_PORT> 和 http://localhost:<PAN_PORT>，PAN_PORT 缺省8768；不得由请求 Host 拼默认允许域。PAN_TERMINAL_ALLOWED_ORIGINS 逗号分隔可替换 allowlist；只接受合法 http/https origin（无 path/query/userinfo/wildcard/null），配置非法 fail-closed，不静默回退放行。
Host 必须与允许 origin 的 authority 匹配；忽略 X-Forwarded-*（不靠转发头扩信任）。Origin 缺失/null/伪相似子域/错端口一律403。Sec-Fetch-Site 若存在只接受 same-origin/same-site；cross-site/none/未知拒绝。POST 必须 application/json（允许 charset）；拒绝 form/text/plain 等 simple request。缺失 Fetch header 不单独拒绝（测试/本地客户端可无，但 Origin 必须）。
PAN_HOST 默认127.0.0.1；非 loopback 绑定默认禁 terminal 功能，PAN_TERMINAL_ALLOW_REMOTE=1 才显式解除该禁用，仍不能绕过上述 gate。此开关不是完整远程鉴权，文档明确不推荐远程暴露。不触动全站 CORS/login/其他路由；浏览器不获取 runner token/pipe/DPAPI秘密。MCP 无 Origin 的接线另批，不添加 bypass header。

## 生命周期/异步 已决纪律

terminal_api 提供可独立测试的 runtime/lifespan helper，依赖工厂可注入，实际 server 只薄接入。每个 app 一个 TerminalService；root 默认 service 的既有默认（尊重 PAN_TERMINALS_DIR），不另造共享目录。非 Windows/绑定禁用不构造 Windows资源，routes503；import 不派生进程。Windows 启动只构造 service 并安排一次 reconcile；启动中 REST503，完成成功才 ready，失败保留 service 引用/静态失败状态，不假 ready。
所有同步 service 方法（含查询磁盘）离开事件循环。每实例最多4个在途请求执行，不设无界等待队列，满则429 busy；任务/线程结果均可追踪。HTTP caller cancellation/disconnect 不取消底层线程、不丢 task/owner；槽位直到真实完成才释放，完成异常被消费。不要以 asyncio wrapper cancellation 假报资源结束。service 自身预算保持，本层不承诺 OS 硬 SLA，不另加 untracked timeout worker。
shutdown 先关 REST准入，再在事件循环外调用同 service.shutdown(budget=20)；startup reconcile 与未完成请求仍留引用/观测。对等待采用 shield + 总预算20s，超时报告 unconfirmed 并保 service/tasks 引用和完成消费，不裸释放、不报成功、不依赖 executor.shutdown(wait=True) 卡住 loop。剩余任务受进程生存限制，不泛化为跨进程可重试。既有其他 server 起停顺序/语义不重构。

## 测试与交付（TA 承担执行责任）

新 pytest 套件为主要验证，不对全部历史做复跑。使用 FastAPI小 app + 注入 fake service 测 route/gate/lifespan；验证实际 server 路由已注册/薄接入（不启动完整 Pan lifespan 插件）。确定性门控：被拒请求零service调用；全部Origin/Host/Fetch/content-type边界；body身份伪造零作用；超大/坏cursor/float/bool/uint64精确；gap/partial/F5/超界；异常哨兵零回显；closing重试；慢方法时其他异步task持续推进；4槽满拒绝；取消后槽不提前释放；reconcile失败不ready；shutdown超时 owner/task保留且迟到结果被消费；非Windows不构造；合法正控不可全拒假过。
另做1条真实隔离 ASGI 链：以临时 root +真实 TerminalService（本 worktree，安装 sidecar专属 npm ci exact pin）创建→read/snapshot→close、同handle身份清理零残留；ASGI无监听可用，不启动8768/完整Pan/账号/provider。REST无input，可用默认shell初始提示作为输出，不新增旁路伪造生产能力。
新 tests 直连 E:/software/miniforge/python.exe 与 uv --no-project --python 同解释器 --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout 各一次（先检查现有依赖，新增临时包需报告）。-o addopts= -q 含实际计数。不跑 service完整真机/launcher/组合/core/全库；需要相邻回归只选相关纯逻辑。
证据新目录UTF8 txt+JSON，最少两日志、关键门控/真实清理事实、源码 blob 锚定与简短README；不要巨量逐同值复制或反复跑来造稳定性数字。开发发现真实故障才先失败→修后，同一正确新功能无需强制造旧版失败。最后单提交、冻结，报告范围/计数/未测与矛盾，TA done不等于MA接受。
禁 stash/reset/覆盖他人文件/按名广杀/push/merge/重启/主线编辑/全局记忆/派子代理；临时进程仅自有同handle PID+raw FILETIME核验清理；旧 node_modules/外部junction不删。
