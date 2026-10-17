# 插件平台整合与 practical 旧数据兼容性验收

2026-10-18，实施工作树 `D:\project\pan-worktrees\plan-plugin-manager-20261017`。

用户授权顺序：最新 main 合入实施工作树；重点检查 practical 真实旧数据；测试通过后合入 main、practical 快进、practical 前端 build。未授权 push 或生产服务 restart，本次不执行这些操作。

## 整合基线

- 实现提交 `bbb9bbe9`：Session 三种 MCP 模式、插件协议与 Provider、共享选择 UI、QQ 薄适配和兼容回归。
- 最新本地 main / practical 基线同为 `9a6e945f28b4a008a80e1f2f269a16aa569e8a7a`。查询 origin 后，本地 main 已包含 origin/main，领先 22 个提交。
- `5676fae4` 将 main 无冲突合入实施分支，保留两边提交历史。
- main/practical 均无 tracked 修改；既有无关 untracked 文档和依赖备份目录保留，不覆盖、不清理。

## practical 实际数据副本

只读采集 `D:\project\Pan` 的实际 config、manifest 和相关数据，复制到实施工作树的 ignored `audit/plugin-platform-20261018/practical-compat/private`。实际数据、凭据及业务文本不提交 Git；公开结果仅记录汇总。

使用新代码及合并后的 manifest 验证：

- **110 个真实 Session**，其中 **7 个选用旧 `pan-qq`**，另保留 `pan`、`lca` 等旧连接。
- **330 次切换/保存/重读**：每个实际选择分别转入三种配置，其中 QQ 切换到 standalone、pan_native、gateway，其他插件原模式不变。
- 检查实际 Session API 序列化、旧连接生成、绑定规范化、配置修订号、持久化重读、再生成 Worker MCP 配置，以及完整历史加载。
- 非插件 adapter 字段、Session 其他持久字段保持；包括 QQ 订阅、报告目标和 outbox、队列、管理关系、模型与 CLI 状态。模式保存过程中 companion history JSONL 哈希不变。
- **12 个实际模板**可解析并生成连接。
- **55 个实际 Job**可读取和规范化，含 **1 个 `send_qq` 动作**；action、状态、调度、目标等原字段保持。没有启动调度器、Worker 或派发任务。
- 副本中的 QQ history/inbox 和 Job 文件哈希保持。原 practical 数据只读；生产仍运行，不能把两个时点原始文件的自然变化当作测试写入。

脚本与汇总：[practical_compat.py](../../audit/plugin-platform-20261018/practical_compat.py)、[result.json](../../audit/plugin-platform-20261018/practical-compat/result.json)。该脚本只操作副本，不使用生产 API；真实账号发送测试属于之前另行授权的验收，不在本轮重复发送。

## 合并后验证

前端相关 7 个文件 **128 tests passed**，包含新建/管理、插件选择、Session 终态和最新 main 的 URL/draft 相关测试。合并工作树前端 build 通过，验证 **68 个压缩资源**。真实 Chromium 复跑插件管理组件场景成功，使用隔离有状态 API fixture，含桌面及 390px 宽度；不是生产服务端到端验收。

后端 **457 passed，4 warnings，161.54 秒**。日志与 JUnit：[merged-backend.log](../../audit/plugin-platform-20261018/merged-backend.log)、[merged-backend.xml](../../audit/plugin-platform-20261018/merged-backend.xml)。包含既有 448 项插件/QQ/Session/持久化/队列 restart 回归与最新 main 的 draft 相关回归。警告为 Pydantic 前向引用和第三方弃用提示；前端仍有既有大 chunk 提示。

实际 practical Pan 解释器可以导入 `jsonschema` 和 MCP，避免新增显式依赖在该环境缺失。生产 Pan、QQ 网关及 SnowLuma 的 PID/创建时间保持，未执行服务 restart。

## 生效边界

已有 Session 不会因读取被批量改成新模式。用户保存模式时才持久化新绑定；QQ 业务数据不搬迁或重命名，旧 Job 和订阅沿用原后端接口。新代码需要下一次正常重启 Pan 服务加载；本次只同步源码并构建前端。正在运行的旧服务尚不能作为新后端已加载的证据。

原生工具列表更新继续采用空闲后 Worker restart；固定 discover/call 连接存在时目录即时生效。独立第三方 MCP 的自主 Schema 更新仍取决于其客户端行为或重启。本轮不新增权限系统、不改变已有 HTTP 上游，也不创建本地 HTTP 执行层。
