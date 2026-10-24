# 插件开发排错

| 现象 | 优先检查 |
| --- | --- |
| 静态检查失败 | 文件存在且 JSON 有效；路径以仓库根/manifest 各自规则解析；字段符合本地 schema；不要把含凭据文档粘进错误报告 |
| helper 未显示在 MCP 列表 | command_ref helper 被隐藏是预期；应看到逻辑插件 ID |
| 插件完全不显示 | 实际 config 的 plugin_manifests 是否包含 manifest，是否加载了另一 checkout/旧服务，后加载声明是否覆盖了同 ID |
| 只修改 manifest 仍看不到新选项 | 在对应隔离实例 reload manifest 或正常重启；确认操作的是该实例，不是 production |
| Worker 工具仍是旧列表 | 看 restart 是否 pending/failed；等待有效 idle，工具缓存依客户端；固定 gateway 与原生刷新不是同一机制 |
| gateway 旧 cursor/摘要报错 | 目录或 Schema 变化；重新 discover，核对合同后决定新调用；未知副作用不能借机重放 |
| API helper 启动失败 | Python/command 是否安装、cwd/import root、command_ref 是否正确；只读静态通过不证明程序可运行 |
| 中文或 JSON 解析失败 | stdin/stdout 编码为 UTF-8，stdout 无 banner/日志，逐行输出、flush，响应 ID 与请求一致 |
| 业务拒绝后连接无法继续 | 是否把普通业务失败作为 JSON-RPC 协议 error；可恢复失败用 result_format:mcp 的 isError |
| PAN_PLUGIN_DATA_DIR 不存在 | 是否走托管 Runtime；独立标准 MCP 直连不经过它的 data 注入；不要回退写用户 home |
| data version/owner mismatch | 停止并核查目录归属与格式；设计备份迁移，不能删 metadata 绕过检查 |
| Panel 不出现或 renderer 报错 | panels 注册和 ID；`account-switcher/v1` 的四动作/effect 与数据契约；`web-app/v1` 是否已成功构建并启用（见 [本地自由 Panel](PANELS.md)） |
| 外部切换返回成功，页面仍旧 | 必须重读外部当前 ID；不同应用、默认账号和绑定账号不是同一个选择 |
| requestId 重复或结果不明 | 同 ID 不同参数应拒绝；pending/失败先核对真实状态，不删台账后重发 |
| 默认账号改变但某配置没变 | 配置可能绑定了特定账号；默认账号变化不解除原产品绑定 |

## 安全提供排错信息

报告插件 ID、版本、模式、动作名、合同版本、预期与实际、时间和脱敏错误；可附静态检查摘要。不要提交 settingsConfig、auth 文件、token、私有 manifest 或真实业务数据副本。provider 的 stderr 也可能敏感，分享前检查。

## “测试通过”分别指什么

静态检查证明声明和 Schema 可解析；假 CLI 集成证明桥接逻辑；浏览器 fixture 证明组件交互；真实只读查询证明该环境可连上原产品；真实切换由授权执行者验证前后 ID。前几层不能作为最后一层的证明。
