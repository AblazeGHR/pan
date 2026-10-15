# Windows 系统通知升级为真实 toast（通知中心）实现与验证

> 本报告描述任务基线与当时的验证范围。基线 `5fa64e2e`，独立工作树
> `windows-notification-center-20261016`，分支 `feature/windows-notification-center-20261016`。
> 未移动 main/practical、未 push、未重启任何用户服务、未访问 8767/8768、
> 未读取凭据、未调用真实 provider。真实 OS 验证使用独立端口（动态分配 + 64177 验收端口）
> 与只读数据库快照。

## 1. 目标与结论

**目标**：旧 `NotifyIcon` balloon 不进 Windows 通知中心、点击仅在 60 秒 helper 窗口内有效。
要求替换/升级为真实 Windows toast/标准通知中心机制，复用现有 system 开关、消息内容与
`?session` 路由，保留点击打开浏览器/指定 Session 深链，并在浏览器关闭、发送进程退出后
仍可从通知中心点击；不得以 API 接受/模拟回调充当 OS 展示证据。

**结论**：已实现并真实验证（少量人工项见 §6）。

- Windows system 通道改为 WinRT toast（`packages/core/windows_toast.py`），完成通知与提醒
  均录入 Windows 通知中心数据库持久保留（实测 ExpiryTime ≈ 到达 + 3 天）。
- 点击目标为 toast 上的「打开会话」按钮（`activationType="protocol"`），`arguments` 是完整的
  loopback 深链 `http://127.0.0.1:<port>/react/?session=<id>`，由**默认浏览器**（系统已安装的
  http 处理器）打开。不注册自定义协议、不创建快捷方式——用户确认的"依托默认浏览器"方案。
- 无每条通知的常驻 helper：发送进程展示后立即退出；点击由 Shell 处理，与发送进程无关。
  同一 Session 完成通知按 Tag 替换、同一内容提醒按哈希去重，避免通知中心堆积重复条目。
- Browser 通道与 `?session` 契约未改动；非 Windows 行为不变（诊断性 unsupported）。

## 2. 调研发现与方案抉择（含用户介入点）

探针与真实实验（`audit/notification-center/probe/`）确认：

1. **unpackaged 身份**：Windows 11 26100 上，以任意 AUMID 调用
   `ToastNotificationManager.CreateToastNotifier(aumid).Show(...)` 即可投递；WPN 平台自动建立
   `HandlerType=app:desktop`、`WNSId=NonImmersivePackage` 的处理器记录并写入通知中心
   （**无需** Start Menu 快捷方式，**无需** `AppUserModelId` 注册表键，**无需** COM 激活器）。
   文档所述"必须有带 AppUserModel.ID 的快捷方式"在本机构建上不成立。
2. **点击激活方式对比**：
   - 自定义 `pan://` 协议 + helper：需 HKCU 协议注册；未注册时 Windows 弹"获取打开此链接的
     应用"对话框（实验中真实出现，已放弃该路线）。
   - toast 主体点击：`<toast>` 元素没有 `activationType` 属性（schema 核实），无 COM 激活器时
     主体点击不产生激活；因此以**动作按钮**为主点击面。
   - **选定**：按钮携带 http loopback URL → 默认浏览器。零协议注册、无"找不到应用"失败面、
     浏览器关闭可冷启动。
3. **AV 事件（真实）**：早期的探针快捷方式 `PanProbe.lnk`（目标为
   `powershell.exe -ExecutionPolicy Bypass -File ...` 形态）被 Defender 判为
   `TrojanDownloader/LNK.Agent.g` 并删除（误报，用户提供截图证实）。据此定下硬约束：
   不创建 .lnk、不使用 `-ExecutionPolicy Bypass`（编码命令本就不受执行策略约束）、
   toast 发送命令不含"下载器"形态。正式实现满足全部约束。
4. **PowerShell 编码陷阱**：PS 5.1 按 ANSI 读取无 BOM 的 .ps1，中文字面量会损坏；载荷
   （标题/正文/XML/tag/group）一律以 base64 字面量传递，脚本本身 ASCII-only。
5. **取证方式**：UIA/截图路线受 UIPI 限制（前台为提权窗口时 `SendInput` 被拒，无法打开/读取
   通知中心面板）。改用**只读 SQLite 快照**取证：复制
   `%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db(-wal/-shm)` 后查询
   `Notification`/`NotificationHandler` 表——这是"录入通知中心"的直接证据，载荷为明文 XML。
   `HandlerSettings` 实测 `s:toast=1`、`s:banner=1`。`ToastNotifier.Setting == Enabled`。

## 3. 实现范围

- 新增 `packages/core/windows_toast.py`：toast XML 构建（XML 转义 + 非法控制字符剔除）、
  tag/group 去重（长度有界）、`-EncodedCommand` 发送脚本（含 `Setting` 禁用检测与失败 JSON）、
  `clear_app_notifications()` 资源清理入口。
- `packages/core/notifications.py`：删除 NotifyIcon 脚本与 helper 消息循环；Windows 默认发送器
  委托 `windows_toast.show_toast`；完成通知携带 `completion-<session>` tag + `Pan-completions`
  group 与深链；提醒携带 `reminder-<sha1>` tag + `Pan-reminders` group；两参数可注入 sender
  契约、非 Windows 诊断、browser 通道与"无 browser 失败回退"均未改动。
- 文档：`docs/USER_MANUAL.md`（集成章节行为描述）、`CHANGELOG.md`（Unreleased/Changed）。
- 测试：`tests/test_windows_toast.py`（新增 11 项）；改写
  `tests/test_notification_closed_browser.py`、`tests/test_notifications_reminders.py` 中
  断言旧 NotifyIcon 契约的用例为 toast 契约（载荷隔离、转义、无消息循环、失败码保持）。

## 4. 真实 OS 验证

- **通知中心录入**（生产路径，`audit/notification-center/evidence-real-e2e.json`）：
  真实 `notifications.dispatch_completion` → WinRT toast → 只读快照查询到行：
  `AumId=Pan.Desktop.Notifications`、`HandlerType=app:desktop`、`Tag=completion-ses_e2e20261016aa`、
  `Group=Pan-completions`，载荷含深链与转义正文（Windows re-serialize 后保留 `&lt;`/`&amp;`，
  文本节点引号还原为字面量）。提醒路径同样录入且无 `<actions>`（无点击目标）。
- **合法目标绑定**：深链由 `completion_deep_link` 构建（仅 loopback、`[A-Za-z0-9_-]{1,64}` ID、
  无 userinfo/query/fragment）；非法 ID/非本机 origin 只禁用点击、保留普通通知（测试锁定）。
- **发送进程退出后点击**：通知中心条目由系统持久持有，点击激活不经由发送进程（无 helper、
  无消息循环、无 60 秒窗口）。
- **人工验收（待用户回报后更新本节）**：01:24/01:31 发布的验收 toast（probe 与生产内容）
  由用户按 Win+N → 点击「打开会话」→ 预期默认浏览器打开
  `http://127.0.0.1:64177/react/?session=...`（点击由 `user-check-access.jsonl` 服务端记录，
  属真实点击证据，非模拟回调）。探针曾尝试以 UI Automation 打开面板并 Invoke 真实通知项
  （OS 中介、非人工点击），因本机前台长时间为提权窗口（火绒日志/安全设置），非提权进程的
  `SendInput` 被 UIPI 拒绝（实测 45 次尝试全部返回 0，Win+N 无法送达），该路线的自动化点击
  与面板像素证据在本机不可用；通知中心录入证据由只读数据库快照独立给出（§4 上两条）。

## 5. 安全边界与注册对象（最小化）

- **正式实现不创建任何持久注册对象**：无快捷方式、无协议注册、无 AUMID 注册表键、
  无 COM/服务/自启。唯一写入是发送时的 PowerShell 子进程（`CREATE_NO_WINDOW`，20 秒超时）。
- 平台侧自动生成的 `NotificationHandler`（app:desktop/NonImmersivePackage）与
  `HandlerSettings` 由 Windows 通知平台管理，等同任何已收过通知的应用；可用
  `windows_toast.clear_app_notifications()` 清空本应用在通知中心的条目。
- **探针遗留（验证结束后清理，见 §7）**：`HKCU\Software\Classes\AppUserModelId\Pan.NotificationsProbe`
  （仅 DisplayName 字符串，探针专用 AUMID；生产代码不使用该键）；被 Defender 删除的
  `PanProbe.lnk` 已不存在。清理 = 删除该键 + `History.Clear('Pan.NotificationsProbe')`。
- 未改动：全局通知权限、DND/专注助手、用户 Terminal、系统安全配置、`pan://` 协议（从未注册成功）。

## 6. 未验证 / 边界（如实声明）

- 通知中心的**面板 UI 可见性**与显示名（用户已在通知中心打开验收 toast，待回报；数据库行与
  `s:banner=1` 支持"可见"，但数据库行 ≠ 面板像素证据）。
- toast 主体（非按钮）点击在无 COM 激活器时不激活；点击面为按钮（与旧 balloon 全区域可点
  不同，属已知 UX 差异）。
- 通知中心条目的系统级删除/保留策略由 Windows 管理；`ExpiryTime` 实测为 +3 天。
- 多 Pan 实例（多个 worktree/端口）共享同一 AUMID；通知条目的目标由**发送时烘入**的 loopback
  深链决定（与旧实现一致），实例端口变更后旧条目的点击目标可能失效。
- Focus Assist/DND 开启时 toast 行为由系统决定，未专项测试；`Setting` 仅为应用级开关。

## 7. 验证事实

- 定向测试（隔离入口 `scripts/run_tests_isolated.py`，Pan venv，`-o addopts=`）：
  `tests/test_windows_toast.py`、`tests/test_notification_closed_browser.py`、
  `tests/test_notifications_reminders.py`、`tests/test_agent_notify.py` → **62 passed**。
- 全量 `tests/`：**3446 passed / 44 failed / 49 skipped**。44 项失败全部位于
  `tests/test_terminal_emulator.py`（43）与 `tests/test_terminal_tools_api.py`（1），
  原因为本 worktree 未安装 `packages/core/terminal/emulator_sidecar/node_modules`
  （`EmulatorStartupError: dependency-missing: Cannot find package '@xterm/headless'`，
  见 docs/TESTING.md 的 sidecar `npm ci` 说明）；用 `git stash` 回到基线提交后同一用例
  复现同样失败，确认为环境性、与本次改动无关。未在该 worktree 安装 sidecar 依赖。
- 真实 E2E：`scripts/run_tests_isolated.py -- <pan venv python> audit/notification-center/probe/real_e2e.py`
  （隔离环境、动态端口、无用户服务交互），`ok: true`，证据落盘
  `audit/notification-center/evidence-real-e2e.json`。
- 探针实验全过程与证据脚本：`audit/notification-center/probe/`；DB 快照（含无关用户通知载荷）
  与桌面截图**不入库**（见 `audit/notification-center/.gitignore`），仅提取 Pan 行作为证据。
- 对应用户截图事件的两处遗留：未注册协议时的"获取打开此链接的应用"对话框（方案已放弃，
  不再触发）；Defender 误删的 `PanProbe.lnk`（已由系统删除且方案不再创建 .lnk）。
