# 终端删除入口

前端“删除终端”与“终止终端”分开：

- 已确认 `exited`：确认后删除记录，不再发送 stop。
- 其它状态：确认“终止并删除”，先调用 close，成功后才调用 remove。
- close 或 remove 未确认：保留记录、显示错误，可刷新/重试；不假删除。
- `lost`、`cleanup-failed` 等状态不能直接删除，避免丢失清理 owner。

新增 `POST /api/terminals/{terminal_id}/remove`，body 为空对象。
沿用 REST Origin/Host/Fetch gate、ID/额外字段校验、有界任务槽和静态错误映射。
成功只输出 `{terminal_id, removed: true}`，不透传服务内部字段；未确认为 409，
不存在为 404。直接调用接口也不能删除运行中的终端。

服务端 `TerminalService.remove` 仅接受权威记录 `exited`，且秘密已不存在、
内存中无 retained runner 身份句柄/业务连接/心跳 owner；先核验后调用 registry.remove。
本次删除的是终端记录与服务内索引，不删除用户工作目录、文件或历史诊断日志。
不改变 registry 层历史接口的规则，不把其允许的 lost 当作服务层清理证明。

验证：前端 Panel + stream 24 项通过；TypeScript 与生产构建通过，36 个压缩产物。
后端/API 初轮 45 项通过；补充 owner 保留负控后最终 **46 passed**（12.83s），见
`audit/terminal/implementation/browser/terminal-remove-api-final-uv.xml`。
包含真实 launcher create → 拒绝直接删除 → close → remove → list 空/秘密不存在。
本次不重跑全库，不重启用户服务，不推进 main/practical。

前端产物已构建，但运行中的后端需由验收者重启隔离验收服务后才能使用新接口。
只刷新旧服务页面可能加载新前端但仍返回接口 404；这不是确认删除。
