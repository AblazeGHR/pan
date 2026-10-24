# 交给编码 Agent 的插件任务模板

复制下面内容，填完方括号，作为单个插件任务的上下文文件。此文件是任务说明模板，不是自动执行的 skill，也不授予生产写入或部署权限。

```text
请为 Pan 开发插件 [plugin-id]，显示名 [名称]。

工作树：[绝对路径]。所有实现和测试仅在此工作树；其他 checkout 只读。
先读取 docs/plugins/README.md、DEVELOPING.md、CHECKLIST.md，
以及 docs/design/Pan-Plugin-Capability-v1.md 和 Pan-Plugin-Panels-v1.md。
检查本目录适用的 AGENTS.md 和当前源码，不把设计提案当成已实现功能。

对接对象：[已有 MCP/CLI/API，源码或只读参考路径]。
这是独立 bridge，不改原产品源码、不打包原产品、不直接改它的数据库。
业务范围：[逐项列出能力]。
Agent 对外工具：[允许暴露的能力]；Panel 内部能力：[需要的额外能力]。
支持模式：[standalone / pan_native / gateway]。
Panel：[不需要 / account-switcher/v1 / web-app/v1（本地构建自由界面，见 PANELS.md）]。
data：[缓存/设置/状态及版本]，不复制外部凭据。

测试用：[自有临时目录、假 CLI/模拟上游或专用测试账户]。
真实查询：[允许或禁止，以及明确范围]。
真实切换/发送/删除/兑换：[默认禁止；由用户执行真机验收]。
不要启动或重启生产服务，不做全局账号/环境切换。

交付：代码、机器可读 manifest/capabilities、静态检查、必要的隔离测试、
使用说明、错误与未知结果处理、用户真机步骤/预期/恢复清单、已知限制。
分别报告源码/模拟集成/浏览器/真实业务验证，不夸大证据。
未授权时不合并；将来合并前先把当时最新 main 合入工作树、处理冲突并复验，
再按授权合回 main，之后才可快进 practical，构建和服务重启分别报告。
```
