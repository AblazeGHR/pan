# CC Switch 官方 GUI Fork 与 Pan 接入

本轮将后端从 SaladDay 5.10.5 CLI 改为独立的官方 GUI Fork。Fork 位于 `D:\project\cc-switch-pan`，基于官方稳定版 **4.0.15**，上游 tag 提交 `79e754ab1b6ed50da57dae1d0faecab4b3b52f1c`。没有修改原 GUI 安装目录、原 GUI/CLI 源码仓库，也没有把 GUI 项目放进 Pan。

## 后端与入口

新 Fork 保留完整官方 GUI，增加控制台二进制和 GUI 的 `--cli` 入口。provider 切换调用官方 `ProviderService::switch`，默认账号调用官方 `CodexOAuthManager::set_default_account`，ChatGPT 额度调用 GUI 同一查询 helper。账号表、身份校验、native token 采纳和令牌刷新均使用 GUI 原实现。

GUI 运行时，控制台经本机 Windows Named Pipe 调用 GUI 同一个 AppState、同一个 OAuth manager；成功后通知 GUI 刷新。GUI 关闭时，控制台直接调用相同服务。GUI/CLI 使用同一路径的进程锁；旧 GUI 无新 pipe 时拒绝并行写入或额度刷新。不增加本地 HTTP 服务。Pan 仍通过 stdio 调用独立 bridge，不导入 GUI 实现或直接读写账号数据库。

安装目录 `D:\openTools\cc-switch-pan`：

- `cc-switch.exe`：新控制台，全局 `cc-switch` 入口。
- `cc-switch-gui.exe`：完整 GUI，前端通过 custom-protocol 内嵌，无需开发服务器。
- `cc-switch-app.cmd`：启动新 GUI。

原 CLI 的两个目录已从用户 PATH 移除；已确认无监听端口的旧 CLI 进程退出。原二进制文件保留，未删除源码或账号数据。旧 PATH 备份保留在仓库外构建目录。现有终端可能保留旧环境，应新开终端；Pan 的插件 settings.json 固定到新程序绝对路径。

Pan 校验 `--version` 中的 `contract: pan-cc-switch-gui/v1` 后才允许实际操作；旧 CLI、纯文本版本或其他协议会被拒绝。MCP 继续只提供 switch/quota 两个业务能力；Panel 另外支持读取列表和设置默认账号。

## 原故障与数据边界

用原 CLI 可执行文件对三个假账号执行 `auth default`，复现 OAuth 存储从 version 2 变为 1，并删除每个账号的 `chatgpt_account_id`、`id_token`、`token_updated_at_ms`。这是完整账号表重写造成的信息丢失，不能仅补一个 JSON 字段就对齐 GUI。它可以解释 GUI 将账号隔离为需重新登录，但不能据此断言服务器撤销了所有 refresh token。

新程序沿用 GUI 原数据目录及可选路径覆盖，不自动重建、迁移到第二套账号库或恢复已删除的字段。只读查询支持 schema 18–20，使用只读 SQLite 连接，不执行数据库启动维护。实际 GUI 启动或需要初始化的业务操作按官方逻辑升级到 schema 20；schema 19→20 增加 MCP 的 Pi 启用列。

已在真实数据库的私有副本上执行升级：原有 9 条 provider 记录，以及 provider_endpoints、mcp_servers、skills 的原有列和值保持不变，OAuth 副本字节不变。真实库未被升级。**升级后的 schema 20 不能由旧 GUI 3.20.4 直接打开，回退需使用升级前备份，不应改低 user_version。**

## Pan 页面

保留三应用页签、搜索、额度缓存、确认切换、请求去重和未知结果处理。新增配置数量、当前配置摘要、图标卡片、备注和分类展示，以及需要重新登录的账号提示；不允许把缺少身份字段的账号直接设为默认。页面操作范围仍以首期账号信息、切换和额度为主，新增或重新认证账号在完整 GUI 完成。

## 验证

本轮执行的检查全部通过；没有执行真实账号或真实 provider 切换。

| 检查 | 结果 |
| --- | --- |
| 新 Fork Rust 入口和真实 Windows pipe 测试 | 7 通过 |
| 官方令牌采纳、v1 隔离、managed/unbound 切换、19→20 迁移回归 | 9 通过 |
| 官方 GUI App、账号卡片和 managed-auth 前端测试 | 44 通过 |
| GUI TypeScript 与完整 renderer 构建 | 通过 |
| Pan 插件/QQ/Session/Panel 回归 | 54 通过，0 跳过 |
| Pan Panel 与共享导航测试 | 17 通过 |
| Pan 变更文件 ESLint 与完整前端构建 | 通过，70 个压缩资源验证通过 |
| Chromium 生产组件的有状态 fixture 验证 | 桌面、390px、查询、取消、模拟切换与搜索通过 |
| Pan 生产 ASGI API → Panel → stdio → 已安装 GUI Fork 可执行文件 | 隔离 home 内配置切换和默认账号切换通过，全部账号字段保留 |
| 三种 MCP 模式 → 真实新可执行文件 | 各两工具，列表调用通过 |
| 真实 GUI/CLI 数据库的私有副本升级 | 19→20 通过，原有业务行和 OAuth 数据保留 |
| 已安装新程序通过 Pan Bridge 查询真实数据 | Codex/Claude/Gemini 列表读取通过；真实数据库、OAuth 与原生配置文件字节不变；未刷新远程额度 |
| practical 的真实数据副本兼容性 | 111 Session、57 Job、333 模式往返、12 模板通过，源目录只读 |

GUI 构建为本机 GNU dev profile 可执行文件，安装副本已剥离调试符号；没有制作或发布官方签名安装包。未在真实数据上启动新 GUI，未把 GUI 的真实交互或真实切换称为已验收。真实切换留给用户按 [真机步骤](CC-Switch-Bridge-Manual-Acceptance.md) 执行；旧记录若缺少认证字段，应由用户恢复可靠备份或在 GUI 重新授权。

证据：

- [已安装程序的真实链路](../../audit/cc-switch-bridge-20261018/gui-fork-installed/result.json)
- [真实数据库副本升级](../../audit/cc-switch-bridge-20261018/gui-fork-data-compat/result.json)
- [真实数据只读查询](../../audit/cc-switch-bridge-20261018/gui-fork-real-read/result.json)
- [practical 数据兼容](../../audit/cc-switch-bridge-20261018/practical-compat-gui-fork/result.json)
- [Pan JUnit](../../audit/cc-switch-bridge-20261018/new-platform.xml)

先将最新 main 合入自己的 Pan 工作树，验证后将本分支合入 main，再将 practical 快进到 main 并构建。最终整合结果记录在 [本轮整合结果](../../audit/cc-switch-bridge-20261018/gui-fork-integration-result.json)。没有推送远程，也不自动重启生产服务。
