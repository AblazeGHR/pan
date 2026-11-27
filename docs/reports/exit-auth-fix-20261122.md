# 2026-11-22：practical 退出与鉴权入口修复

基线：practical `1f3f525d282697e1a9a614a42a1c8aa74142421f`。
范围：隔离工作树验证、本地提交后仅快进 practical；main 不动，不 push，
不操作真实服务。既有用户修改、删除与未跟踪内容不进入提交。

## 根因与代码修复

1. 旧 stop 脚本无凭据请求受全局鉴权保护的 HTTP Exit，因此正常退出入口
   与登录保护不兼容。现在包装当前检出的 CLI，使用已有同用户 Core IPC；
   不保存密码、不过网络免登录、不扩大 IPC 白名单。CLI 等待 Job 成功终态
   和原实例消失后才成功，保留 ask 策略要求，不自动强杀或转内部 shutdown。
2. 旧监听查询只按端口取首个 PID；现场同端口不同地址时选中 Tailscale，
   Exit Job 的 oldPid 错误。平台层现在按地址+端口匹配，显式处理 wildcard、
   IPv4-mapped IPv6、多 PID、未知 PID和查询权限失败。查询失败不是无监听。
3. Exit/Restart 的 durable Job 从接收请求的主进程记录 PID/创建时间，不再
   从系统端口表猜测自身。supervisor 仍独立核验身份。内部 shutdown 同样
   绑定预期 PID/创建时间，陈旧身份不得关闭继任进程。
4. 启动记录 bindHost；CLI、就绪和停止检查沿用该地址，避免读取外部 Agent
   的环境。退出会清除 process.json，CLI 因此在开始时冻结旧地址，供最终确认。
5. 诊断区分 authentication_required、authorization_rejected、timeout、
   connection_failed 和 response_error；401/403 不再被当成无响应。

## 浏览器症状的新证据

原 practical dist/index.html SHA-256：
`7ae4542ec31f82499817a4c1c36b14be5bcd0b277a51109af1ce909b5fd486dd`。
将原产物复制到隔离树后，同一现有浏览器登录测试无法找到登录边界，
页面已渲染业务框架；该产物 JS 中未找到当前 LoginBoundary 的登录标题。
隔离树以当前源码执行 pnpm build 后，同一测试通过，包括登录、持久 Cookie、
冷重启、注销及业务访问拒绝。未修改前端源码、Cookie、密码或全局鉴权。

这支持前端构建产物与鉴权源码不匹配是页面“壳可开、数据被拒绝”的主要原因，
但不能还原当时用户浏览器全部 Cookie/域名状态。隔离构建成功不等同于已经
更新 practical 的 dist；实际产物更新按用户另行确认执行，且不自动启动服务。

## 验证与持久防回归

- 新增最小复现：旧实现三项确定失败；新实现不受系统枚举顺序影响。
- 新增真实隔离进程：未登录网络 Exit 401，本地 CLI→IPC→真实 supervisor
  →退出 Job offline/completed；另一地址同端口的独立进程持续存活。
- 同一地址多 PID/缺失 PID/查询失败明确拒绝；陈旧 IPC 身份拒绝；主进程
  创建时间缺失时不创建退出 Job、不关闭 Worker gate。
- stop 批处理在带空格/中文的临时检出中调用正确入口，并传播退出码。
- 相关日志/JUnit、红测统计与构建记录在 `audit/exit-auth-20261122/`；不提交
  原始真实用户事故日志、会话、Cookie、密码或账号数据。
- Windows 上运行契约、真实 socket、真实 HTTP/IPC/Job 和浏览器登录测试。
  macOS/Linux 目标平台运行与远端三平台 CI 未执行，不宣称已通过；原有
  service.lifecycle 能力边界保持 Windows-only。

本次未改 scripts/restart.bat 的旧 HTTP 包装；正常重启请使用已注册的
`pan restart`（同一 CLI/IPC 和修复后的身份链路），不推断未认证 HTTP 可重启。
后续涉及登录边界的源码更新应在目标检出构建前端，并以未登录浏览器验证
“先显示登录页、业务 API 仍拒绝”，不能只看后端测试或静态壳响应 200。
