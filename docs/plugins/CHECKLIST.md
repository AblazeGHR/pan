# 插件交付检查表

可复制到插件自己的验收报告；未执行项写“未验证”，不要默认勾选。

## 声明与协议

- [ ] 固定插件 ID、版本、能力 ID/export_key；无保留名、别名或导出冲突。
- [ ] 明确支持模式与默认模式；标准 MCP 非 tools 需求限制为 standalone。
- [ ] 输入 Schema 拒绝多余参数，输出 Schema 与实际 structuredContent 一致。
- [ ] command/args、HTTP endpoint/path/method 固定，不接受调用者任意执行目标。
- [ ] 静态检查器通过；还在实际 manifest 组合下检查覆盖顺序。
- [ ] helper 使用 UTF-8，stdout 无日志；初始化、ID、业务错误和超时行为可复核。

## Session、Panel 与数据

- [ ] A/B Session 同时接入不串 Schema、路由或账号上下文。
- [ ] 关闭工具后不可继续调用；不会关闭原产品服务、Job 或全局 Panel。
- [ ] API 的 agent_capabilities 在三个模式一致过滤，隐藏能力不能直接调用。
- [ ] Panel 的 renderer 契约满足：`account-switcher/v1` 检查四动作/effect；`web-app/v1` 检查 entry、build argv/cwd/output、构建成功/失败/重复/Unbuild 与相对资源、MIME、SPA 回退。
- [ ] 重复 ID、卸载旧 URL、未构建不可启用和导航窄屏均验证。
- [ ] data 目录由宿主分配；owner/version 不匹配拒绝，无隐式迁移或删除。
- [ ] 缓存没有 token/API key/原始配置，页面显示查询时间和失败/未知状态。
- [ ] 多 helper 的共享写入有并发方案，外部产品并行操作边界有说明。

## 副作用与验证

- [ ] 初始测试使用自有目录、假 CLI/临时数据库/模拟上游，绝不复用用户账号执行写入。
- [ ] 超时、断开、进程退出后不自动重试副作用；界面不会误报回滚。
- [ ] 面板 requestId 去重及旧状态校验通过，清楚说明 MCP 不经过面板台账。
- [ ] 原生/独立 restart 与 gateway 目录更新分别验证，不宣称客户端连接内即时刷新。
- [ ] 实测范围、命令、版本、证据、已知限制及清理结果齐全。
- [ ] 对真实写操作交付用户可执行的前置记录、步骤、预期与恢复清单。
- [ ] 若需整合：最新 main 已合入实施分支，相关回归通过，practical 实际数据只在隔离副本验证。
- [ ] main 合并、practical 快进、build、push、服务 restart 分别按授权执行和报告。
