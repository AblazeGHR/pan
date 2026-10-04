# P2 第一批：TerminalService 与 registry 生命周期闭环

## 目标与起点

用户已要求继续推进。先交付可被后续 REST/WS/MCP 调用的服务控制层，不在本批接服务器、MCP 或前端。
注册树 `D:/project/pan-worktrees/terminal-service-implement-20261003`，分支
`implement/terminal-service-20261003`，代码基线 `475395c3dddabdcb74c31932bd747932a8b3c1da`。
开始必须核对 root/branch/HEAD/status，读取 CODEBUDDY.md、本 brief、实施计划全文、
launcher/runner/IPC/core 当前接口文档；计划与新验收有冲突时以最新接受边界为准。

## 写范围

- 新增 `packages/core/terminal/service.py`。
- 新增 `tests/test_terminal_service.py`。
- 新增 `docs/design/PAN_TERMINAL_SERVICE_INTERFACES_20261003.md`。
- 新增 `audit/terminal/implementation/service/**`，旧证据不改、不再生。
- 仅若真实 cwd/shell 配置接线必需，允许最小修改 `launcher.py`：新增可选参数并传给已有
  TerminalRunner(cwd, shell_argv)，保持默认行为；相应补 `test_terminal_launcher.py` 和 launcher 接口文档。
  不改其清理机制；不改 runner/client/IPC/contracts/registry/attachments/backend/emulator/sidecar。
- 不改本 brief、__init__、server/MCP/frontend、依赖清单/总锁、background_jobs、.workflow。
  冻结接口缺少必需能力时先报告矛盾，不私自扩共享协议或以不安全旁路绕过。

## 必需功能

1. TerminalService 的 create/list/get/read/snapshot/input/resize/close/detach/reconcile/shutdown；
   可注入原语便于确定性测试，但默认生产路径必须真实 launcher + pipe/HMAC/DPAPI。
   无副作用导入，不在 import 时建数据目录或创建线程/进程。
2. 创建记录先持久化、容量准入默认 8（并发不得超限）、唯一 term_id，不覆盖既有记录/秘密。
   默认 cmd.exe /q /d；cwd 指定到临时目录的真机验证。scope 仅元数据，created_by 来自调用上下文，
   不从目标 session_id 推断。启动四门确认前不发布 RUNNING。
3. 通过生产 launcher 派生；argv/env 不含 token。不以 Popen.pid 当 runner 权威：
   hello 自证 + 同句柄 pid/raw FILETIME/Wait 内核核验后写秘密；端点身份核验 + HMAC 后才业务请求。
   launcher 与真实 runner 在 shim 场景可能不同，清理各自身份，不按命令行/进程名杀。
4. 独立心跳连接、稳定 client_id，间隔 1s/死期2s；慢 snapshot/write/close 不拖住心跳。
   服务线程/异步调用封装有界，不在事件循环直接执行阻塞原语；未完成 worker 可追踪、跨重试复用、
   不叠加。公开 async 或 sync 面选一种，接口文档明确实际预算与线程纪律。
5. AttachmentRegistry（浏览器控制 lease）与 runner IPC 所有者心跳分层。借代理接入冻结
   attachments 协议；input/resize 校验实际发出的 lease，同临界区校验+调用，撤销后零新写。
   observer 不可 input/resize；断开 attachment 仅撤销，不 stop runtime。
6. read gap 不补零、不跳 cursor；协议 A snapshot 保留 data_b64/applied cursor、partial/degraded/note
   与 F5 bool-or-null 字段，不把 diagnostics.reasons 空当 full。applied 被驱逐返回显式 gap/fresh-view
   提示，不自动 reset、不杀 PTY。resize 保持 PTY/引擎确认分列，不宣称三方一致。
7. 显式 close/shutdown：先记录 CLOSING（按现有 RuntimeState 合法枚举映射计划 stopping），
   向 runner stop，等真实清理确认 + launcher 引擎收尾和经身份核验的退出；不能只认 response ok/Popen exit。
   未证明 => CLEANUP_FAILED 或 CLOSING + 静态原因、保 owner/record/secret 可重试，不标 exited/lost。
   只有已证明终止后才按 SecretStore 约束删除秘密；无确认不得用 caller-responsibility 选项绕过。
8. detach 真实 ambient Job 当前不支持：拒绝时零状态变化；不新增 breakaway/环境逃脱。
   注入的已 detached 生命周期可以验证逻辑，报告明确机制注入，不能称真实 durable 验收。
9. reconcile 核验 registry/secret/端点与 raw identity：未知/错身份零终止且保诊断；
   fresh PID 不存在不是 retained DEAD 证据。旧 managed 先等死期并确认或重发 stop；detached 核验后重连；
   不创建替代同 id runner、不复活旧 lease、不因客户端断连删秘密。已死、无法归因与 cleanup 未确认分列。
   正常 shutdown、快速重启和崩溃路径符合计划§5.5，但不伪造整树或引擎清理事实。
10. 公共返回值不得暴露 token/pipe/秘密内容；错误/诊断仅静态原因和类型名，界限有界。
    scope 非权限；本批服务上下文标明 trusted local/controller 与 attachment 角色，
    实际 Origin/CSRF/MCP caller gate 留下一批接线，不声称当前有 Web 鉴权。

## 测试与证据

- 确定性：并发 create 容量/重复id、启动部分失败保 owner、心跳不被慢业务拖死、控制权撤销/迟到输入、
  close迟到结果与重试不重叠、shutdown预算含锁等待、未知身份零触碰、记录写失败、秘密不提前删除、
  detach拒绝零变化、gap/F5不升级、reconcile不冒充恢复或丢未收敛资源。
- Windows 真机隔离临时数据根：默认创建/真实cwd/中文输入和read/snapshot/独立心跳/断连同PID/
  显式close整树+sidecar收尾/正常shutdown/服务宿主崩溃后lease自停/新实例reconcile。
  只启动自有无provider的测试服务宿主，不使用8768，不启动既有Pan或完整Web服务。
- 运行 service 套件直连与 uv 隔离各一次；若改 launcher 参数，运行 launcher 相关定向及最少真机cwd对照。
  相邻 registry/lease 逻辑测试各一次；不重复全库/所有旧方向或多轮稳定性刷证据。
- uv --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt
  --with pytest --with pytest-timeout --with pyte==0.8.2 -- python -m pytest tests/test_terminal_service.py -o addopts= -q。
  Node 只在 sidecar 专属目录 npm ci（exact pin/不改锁，node_modules不提交）。
- 故障回归先失败后通过；实现新增可记未实现基线，不把测试笔误冒称产品缺陷。
  UTF-8 .txt 日志入库，source blob 锚定、passed/failed/skipped/unverified 分列；仅新service audit目录留证。
  有外部junction等未跟踪物时保留，不称全clean。

## 安全与交付

只清自建进程/临时目录；终止须同handle raw FILETIME + Wait，不按PID单值/名字/命令行广杀。
禁止git stash/reset/checkout覆盖，不派子agent，不碰其它树/main/practical/.workflow/8768/既有服务/账号。
apply_patch编辑；单一交付提交后冻结并集中报告：范围、接口矛盾、实测、失败、未测、清理、源码锚定。
不 push/merge/restart。Ctrl-C、真实durable、浏览器、跨用户/主机、长稳仍未验收。
