# Job 命令窗口策略

Windows 上新建后台命令 Job 默认 `hideWindow: true`；MCP 参数为
`hide_window=True`。需要可见命令控制台时显式传 `false`，或在 Job 创建界面
取消勾选 **Hide command window (Windows)**。定时 shell 使用 `action.args.hideWindow`。

旧记录缺字段时按隐藏处理，不迁移或改写历史数据；重试保留原策略。
非 Windows 保留配置但不改变进程窗口行为。配置必须是真布尔值，不接受字符串或数字。

隐藏针对 Pan 启动的命令控制台：`CREATE_NO_WINDOW` + `SW_HIDE`。
显示使用 `CREATE_NEW_CONSOLE` + `SW_SHOWNORMAL`。外层独立 runner 的脱离和
Job 所有权逻辑不变；不把 `CREATE_NO_WINDOW` 与 `DETACHED_PROCESS` 混用。
此选项不保证抑制命令自行创建的 GUI 窗口，也不赋予交互 stdin（仍为 DEVNULL），
不改变 Job 存活、通知、取消或重试的保证。

需要加载更新后的 Pan 后端才对新请求生效；现有运行中 Job 不动态改变窗口。
API/MCP、旧 runner、独立 runner 与定时 shell 均使用同一策略。

验证包括旧记录默认值、严格类型、API/MCP 透传、重试和幂等旧 payload 兼容，
以及 Windows 真实子进程 `GetConsoleWindow` 的隐藏/显式显示正反控。
