# 全站单管理员认证

任务基线 practical d88df38e / main 74f47525。两树独立适配；本文为实施设计及验收索引，未验证项不代表通过。

## 已核实边界

现有身份是 automation.UserAuth；Cookie、Bearer 共用持久会话及四对象权限。原 Cookie 中间件只处理 HTTP `/api/`，无全站拒绝。现 `user.key` 明文；缺失时 key() 自动生成。首次配置必须改为显式本机操作，HTTP 不提供注册。旧密码只有在成功证明后迁移，保留已验证旧会话的 token 身份，并随机化旧密码派生的 revision。不得读取真实 profile。

机器调用：MCP `_api`、terminal/self-queue 专用客户端目前为无凭据 HTTP，SessionId/header 只是声明；四对象已使用 plugin IPC + 私有 scope marker。CLI/launcher/console_exit 为另一组无凭据 HTTP。Job registry/通知主要进程内，独立 Job 服务是另一协议，不能重写。QQ bind/unbind 回调 Core；插件动态面板及 provider 路由也须被统一入口覆盖。

## 实施契约

最外层 ASGI 中间件处理 HTTP 和 WS，不以路由前缀、来源 IP、Host、Forwarded 或 SessionId 授权。公开面只允许登录外壳和构建静态资源、原登录/会话状态及无业务信息 liveness；业务静态、文件、下载、OpenAPI/docs 均受保护。动态挂载不能绕过。

复用 UserAuth，会话只存 token 哈希。密码使用随机盐 scrypt，原子落盘；受控本机 initialize 接受交互输入，不自动输出凭据。旧 user.key 验证成功才迁移，失败不写。会话、密码修订及撤销持久化故障须 fail closed。WS 握手认证、严格 Origin、连接存活期间复检，过期/退出/改密关闭；Cookie HTTP 写必须同源，显式 Bearer 保留机器以外既有用户客户端兼容。

机器使用原 IPC 或明确作用域凭据，不生成全局管理员机器 token。IPC 仅由白名单操作进入原路径，保留 MCP managed/capability 与 canonical owner gate；用户授权与 Agent 四对象授权分开。CLI 仅生命周期/实例查询作用域。不存在凭据时显式失败，不回退无凭据 HTTP。

Host/代理只用于 Origin 与 Cookie 安全属性，绝不成为身份。不得盲信代理头。代理部署细节在隔离证据中验证，现隧道配置保持原状。

## 用户已澄清的期限规则

2026-11-16 用户明确：0 继续表示会话 Cookie，n（7/30/90）继续表示 n 天内持久有效。保留当前默认0与所有已有设置，不做默认值变更或旧值迁移。配置 n 后，浏览器重开与 Pan 冷重启仍使用原持久会话；持续使用按原半程续期规则续期。原待决卡已标记 no_answer_needed。

用户另已明确：本次只保证本机 stdio MCP；远程 gateway 另行决策，不自动给远程协调者任何身份/权限。

## 覆盖矩阵

| 面 | 未认证 | 用户 | 机器 | 验证 |
| --- | --- | --- | --- | --- |
| 登录外壳/必要资源 | 最小公开 | 同入口 | 不提升身份 | 待红绿/production Chromium |
| 业务API/配置/Session/history | 401 | 原权限 | scoped IPC + 原managed | 待真实HTTP/MCP |
| 文件/下载/附件/static | 401 | 原资源gate | 默认拒绝 | 待直接URL负控 |
| WS/agent/terminal/native | 握手拒绝 | Origin + 会话复检 | 原IPC/显式scope | 待真实WS撤销 |
| 动态插件挂载 | 默认拒绝 | 原插件gate | 原binding/IPC | 待动态挂载负控 |
| 四对象/Action Jobs | 401 | 原UserAuth | 原permissions/owner | 待相邻回归 |
| CLI/launcher/console_exit | 拒绝无凭据 | 原gate | 生命周期专属scope | 待隔离CLI |
| Job/通知/QQ/MCP链 | 拒绝外部伪造 | 原gate | 原IPC/原receipt | 待隔离Job终态通知 |
| 冷重启/续期/退出/改密 | 失效拒绝 | 持久修订 | 不获得用户身份 | 待存储失败/并发负控 |
| Windows/POSIX | 同协议 | 同协议 | pipe/socket原平台层 | 待契约/矩阵 |

## 交付边界

仅两个候选分支提交；记录每树完整基线diff、SHA、红绿命令/证据及剩余边界。无push、集成、部署、真实服务操作或真实数据迁移；main既有P7A两红独立记录。启用前由操作者备份认证文件并在本机初始化/证明旧密码；回退需匹配源码及认证存储格式，不能把旧明文备份对外公开。


## Core 外围进程

QQ/WeChat → Core 的 HTTP 与 QQ WS 回调使用进程期随机凭据，只注入对应子进程。凭据限定频道通知、该频道原命名空间 Session 读/建/派发与 QQ worker.result 订阅；不能访问管理员设置、用户会话撤销、另一频道或普通 Session。凭据不进入 Worker 全局环境。原 IPC Core/CLI 路径不使用机器管理员 Bearer。

独立 QQ/WeChat 管理 HTTP API 及 Remote 状态服务的封口范围已向 MA 提问，待确认；不将 Core 全站通过冒充这些独立进程也已受保护。上游 OneBot/iLink 协议不在已确认修改中。远程 MCP gateway 已明确另行决策。

practical 仅为本功能新增可信 Core IPC 时补齐短路径、0700 POSIX socket 目录和失败清理；没有移植 main 的 canonical Job/Interaction 架构。

## 操作者启用与回退

本候选不自动部署或重启。启用前停止写入并备份 auth 目录；已有密码无需更改，成功登录会迁移为随机盐 scrypt 并保留有效会话 token 身份。未配置管理员时，在该实例电脑的交互终端执行 `python -m packages.automation.admin --auth-root <该实例automation目录>`，输入密码不会回显、不会放到命令行或日志。不得经公网首次注册。

从同一个正常 Pan URL 登录，`0` 为会话 Cookie；在授权设置选择7/30/90后，浏览器重开与服务重启保持有效，持续访问续期。退出只撤销当前会话，revoke/改密撤销所有会话，已有WS最多约1秒关闭。

HTTPS终止代理须在候选配置 `global_auth.origins` 中显式列出完整HTTPS origin，例如 `https://pan.example`，并保持原Host；忽略Forwarded/X-Forwarded头。这里不改真实域名、隧道或配置。该来源仅决定Cookie安全属性/CSRF匹配，不提供身份。直接本机HTTP不需要此字段。POSIX认证状态必须放在可落实0600的文件系统；不支持时拒绝存储。

回退须同时恢复旧源码与同一时间点的认证目录快照：旧源码不能读取scrypt verifier。恢复备份后撤销旧会话并在受控本机再次启用。不能仅回退源码后把散列字符串当密码，不能把明文备份上传。机器运行期凭据随进程重新生成；回退不重放任何Job/Action/通知。
