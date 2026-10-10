# Codex 同内核控制切换

## 范围与使用

独立集成树 `D:/project/pan-worktrees/codex-native-control-integration-20261010`，
分支 `integrate/codex-native-control-20261010`，从 main `4b166bd3` 分叉。
用户授权最终验收后只合入 main，不合 practical、不 push、不重启实用服务。

Windows 上 New Session 选择 Codex 后，可显式勾选同内核 Terminal TUI。
既有 Session 缺省不开启；其它 adapter 不提供此能力。
点击 View 旁独立的 **Terminal TUI**，请求实际控制权转移；点击任一 Pan View
交还控制权。切换不重启、不 resume 新内核、不 interrupt 当前 turn。

## 实现与所有权

- 默认 Codex stdio 路线不变；只有 `adapter_config.native_control is True` 使用
  私有认证 WebSocket app-server。浏览器不接触内核端点或 capability。
- app-server 与常驻真实 `codex --remote` TUI 分别在自持 ConPTY Job 中启动，
  suspend → assign → 成员/身份证明 → resume；失败保守处理，收尾核验整树。
- TUI 初次 attach 获取同一已加载 thread 的权威 `thread/read`，不重新恢复内核。
  全新且确认无 turn 的 thread 可投影 schema 正确的空历史页；恢复/未知 thread
  不据此伪造空历史。TUI 与 Pan 都订阅同一执行流。
- `ControlOwner` 的模式/generation 检查与真实写入同锁；所有 TUI 变更经代理，
  只允许当前 thread 的交互面。旧世代输入/审批拒绝，待审批切换后重新发出。
- 私有 capability 使用随机值，内核 argv 仅摘要，TUI 仅通过专用环境变量读取。
  端点选择存在 bind 竞态，认证失败即拒绝，不接管未知 listener；连接丢失不
  自动重建内核。CLI WebSocket/remote 是实验接口，升级 Codex 必须复验。

## Worker、队列与结果

模式独立于 Worker status/held 与 Session.queue_paused。切换、原生输入和实际
队列投递共享准入门；启动 TUI 前就建立 fence，原生 turn 请求发出前置 busy。
确认丢失保持 switching，不能仅靠前端切换样式放行。

队列内容不删、不重排。TUI 模式自动 handoff 被后端挡住；回 Pan 后只有原生
turn 已完成且原有手动暂停允许时才恢复。原生 turn 使用新的任务序号，清除上一
Pan 队列项/任务的来源关联，正常持久化 user/assistant、lastResult 和订阅报告。
native/uncertain/原生在途状态不因 Pan 空闲 watchdog 回收内核。

## 界面与浏览器生命周期

Terminal TUI 与现有历史 TUI View 是不同入口。native/switching 时三个 Pan View
均无选中态，合法全局 `chatViewStyle` 保持；真实 Pan ack 后才显示目标 View。
主区域隐藏历史、输入栏、banners、搜索/导航/rewind，仅嵌入真实终端并保留底部
durable queue。队列始终展开但不改变 Pan 保存的面板偏好；复用编辑租约、暂停、
锁定、增删排序，native 不提供 Steer 绕过控制门。

HTTP 输入/resize 有 Worker identity 与 generation fence。UTF-8 粘贴按 code point
分块，输入序列不重排。输出以绝对 cursor 从 4 MiB 回放环读取，缺口明确警告
显示可能不完整，不宣称屏幕权威恢复。浏览器刷新/切换 Session 仅 dispose 显示
连接，不关闭 TUI/kernel，不发送 control mode 或 stop。

## 验证分层

Python 经 `scripts/run_tests_isolated.py` 清除继承 PAN 环境，所有真实用例使用
私有临时 CODEX_HOME、合成凭据、loopback 合成 Responses provider，不调用真实
模型/用户 Session。两条真实 CLI 链分别覆盖 Pan 启动与实际 TUI 键盘启动：
执行中多次切换，thread、内核 PID/raw FILETIME、TUI PID 不变，释放合成响应后
任务自然完成。其它回归覆盖审批、队列准入、历史/结果/报告来源、HTTP 与旧配置。

Chromium 用真实 ChatView/TopBar/xterm/queue 组件和拦截的 API fixture 验证界面：
原生输入发出、无 View 选中、历史/composer 隐藏、底部队列可见、刷新不改控制、
回 Pan composer 恢复。**这不是浏览器到真实 provider 的合并端到端证据。**
最终长回归日志/退出码/源码锚定保存于 `audit/codex-native-control/20261010/`。

## 兼容与限制

### 现有 Codex Session 的升级与降级

所有 Codex Session 均显示 View 旁的 Terminal TUI 入口。普通 stdio Session
点击后先确认升级；Worker Settings 提供 Session 降级入口，不把配置绑定在临时
Worker 上。`POST /api/sessions/{id}/native-control/configuration` 只接受
`{"enabled": true|false}`，沿用 native-control 的来源门与 Codex/只读校验。

活 Worker 采用 **delay**：持久化 `adapter_config.native_control_next`，暂不改变
当前 `native_control`。当前任务继续执行，等原生在途 turn 完成、终态落盘和队列
单元收尾后，在真正 idle 边界退休旧运行时，再原子应用配置并启动替代 Worker。
期间阻止新的队列 handoff，不删队列、不重排、不清 thread/history；相同请求
幂等，反向请求在已有转换待生效时返回 409。原生输入所有权可先还给 Pan，
这不是中断当前 turn。旧运行时未确认停止则保 owner、保待生效配置，拒绝第二
内核。启动新运行时仍可能失败；配置生效不冒充新内核已就绪。

无活 Worker 时仅保存配置，不自动 spawn。升级固定 stream 连接；降级恢复标准
Pan 控制并保留 stream 配置。服务重启后 pending intent 仍在，下次合法 spawn
先应用；不依赖进程内 delayed-restart 投影。GET status 对普通 Codex Session
也可读，包含 `enabled`、`pendingEnabled`、`delayedRestart`。前端等待服务器确认
已生效才进入 TUI，刷新页面会失去自动进入意图，但不会丢失持久转换配置。

转换会在 idle 重建 Worker，因此不是跨连接方式的同 PID 保证；同一 Session/
Codex thread/历史/队列保持。已升级 Session 的 Pan↔原生输入控制切换仍沿用
前述持续执行内核协议。后台 Python 全量与前端验证使用隔离数据根，浏览器升级
门控仍为真实组件加拦截 API fixture，不声称浏览器到真实模型链已验收。

不迁移旧 Session、不改变旧 adapter 缺省连接或已有队列身份/断言。新字段缺省
false，服务端只接受 Codex 的真 bool opt-in。main 合并不代表 practical 已部署。

真实验证限 Windows / 本机 Codex 0.161.0。未验证真实付费模型、浏览器+真实
provider 完整链、跨用户/主机、POSIX、长稳、跨内核重启恢复。调用方等待预算
不是 OS 硬 SLA。通用 Terminal Ctrl-C 与 durable detach 的既有产品门不因本功能
改变；本功能是常驻原生 TUI 控制切换，不是向任意 shell 的 OS 中断保证。
