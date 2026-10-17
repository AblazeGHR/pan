# CC Switch Bridge：main 整合与 practical 兼容性

2026-10-18。用户本轮授权：最新 main 合入实施工作树，处理冲突、验证 practical 旧数据后合回 main，再使 practical 快进；沿用既有约定构建 practical。不包含 push 或生产服务重启。真实 cc-switch 切换仍只由用户验收。

## 合并范围

- 功能提交：`5432a338`，含 Panel 协议、插件数据目录和 CC Switch Bridge。
- 合并前 main/practical 同为 `52614cd4a3184f3e7b3befb4c9f50f08383c25f3`。fetch 后本地 main 包含 origin/main，领先 32 个提交。
- `bf563b2a` 把该 main 无冲突合入实施工作树。审查了共享 Web lifespan、插件路由、Session 筛选导航和延迟重启设置，保留 main 的新 Session 筛选菜单及重启修复。
- 两个目标工作树均无 tracked 修改。既有未跟踪文档和 node_modules 备份目录不覆盖、不清理。

## practical 数据验证

采集时源 checkout 为 `D:\project\Pan`，Git HEAD 为上面的 `52614cd4`。只复制相关 config、manifest、Session、QQ history/inbox 与 Job 记录到当前工作树的 ignored private 目录。生产服务保持运行，副本是逐文件采集，不声称跨所有活动文件的事务快照。未读取或复制原 cc-switch 数据库。

通过 [兼容性脚本](../../audit/plugin-platform-20261018/practical_compat.py) 的独立输出目录参数运行，避免复用上轮已修改的测试数据；同时兼容已有 v2 插件绑定的测试预期。

- 110 个实际 Session，其中 7 个含旧 `pan-qq`；保持旧 `pan`、`lca` 等选择。
- 330 次模式保存、重读和连接再生成，包含完整历史读取和实际 Session API 序列化。
- 12 个模板可解析；56 个 Job 可读取和规范化，包含一个 `send_qq` 动作；没有运行调度器或派发任务。
- QQ 订阅/通知/outbox、管理关系、队列及非插件 adapter 字段保持；模式保存不改变 companion history JSONL；QQ 与 Job 副本哈希保持。
- 新 Panel 注册不改变已有 Session 工具选择。新的 helper 不作为独立可选 MCP 插件出现；Agent 仍仅暴露 bridge 的 switch/quota，Panel 使用完整内部能力。

结果：[practical 汇总](../../audit/cc-switch-bridge-20261018/practical-compat-integration/result.json)。private 目录及真实业务数据不提交 Git，公开证据只有统计。

## 合并后检查

前端 13 个相关文件 **192 项通过**，包含 Panel、导航、最新 main 的筛选菜单/adapter filters、TopBar 和延迟重启设置。工作树 build 通过，验证 70 个压缩资源；真实 Chromium Panel/导航组件复跑通过，使用拦截的有状态 API fixture，真实 cc-switch 命令执行数为 0。

后端 **511 passed，4 warnings，146.83 秒**，见 [JUnit](../../audit/cc-switch-bridge-20261018/integration-backend.xml)。覆盖插件/Panel/bridge、旧 MCP、QQ、Session 持久化、handoff、队列延迟重启，以及 main 新增的锁定队列 HTTP 和 adapter 校验用例。前后端测试不累计重叠轮次。警告为既有 Pydantic 前向引用及第三方弃用提示。

## 生效与剩余验收

生产数据未批量迁移，原始 cc-switch 未改造。快进和前端 build 不代表旧 Pan 进程已经加载新后端；新后端在下次正常服务重启后加载。本轮不调用生产重启 API，不执行真实切换。由用户按 [真机测试清单](CC-Switch-Bridge-Manual-Acceptance.md) 执行账号/配置切换与恢复验收。
