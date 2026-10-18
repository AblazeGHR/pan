# 无网络 API 插件示例

本目录可独立复制，包含 manifest、能力文档和仅使用 Python 标准库的 helper。它只做 `one → 1`、`two → 2` 的 lookup，不查询真实账号、不执行切换、不写业务数据。

在 Pan 仓库根执行（使用已安装项目依赖的 Python）：

```powershell
python tools/validate_plugin.py docs/examples/plugin-capabilities/manifest.json
```

要做真实协议冒烟，手动向示例 helper 写两行 JSON：

```powershell
@'
{"jsonrpc":"2.0","id":1,"method":"provider.initialize","params":{"protocol_version":"pan-capability/v1"}}
{"jsonrpc":"2.0","id":2,"method":"capability.invoke","params":{"operation":"lookup","arguments":{"item_id":"one"}}}
'@ | python docs/examples/plugin-capabilities/provider.py
```

预期分别返回协议版本和 `{"item_id":"one","value":1}`，响应 ID 为 1、2。此命令只运行本目录的示例。

接入隔离 Pan 时，在原 `plugin_manifests` 列表中追加此 manifest 路径，不删除默认和私有项；reload 后在测试 Session 勾选 `example`。三个模式的名字分别是 `example.lookup`、`pan_example_lookup`、gateway 的 `api_call(name="example.lookup",arguments={"item_id":"one"})`。原生接入配置变化需空闲后 restart。

复制后修改插件 ID、helper ref、capability 前缀和 export_key，使它们一致。加 Panel 前先看 [开发指南](../../plugins/DEVELOPING.md)；这个例子没有 Panel，不会创建一个无法满足四动作契约的假入口。
