# MCP remote connection / MCP 远程连接

Pan can manage an independent authenticated MCP gateway and a named Cloudflare tunnel.
应用设置中的 **MCP 远程连接** 管理独立网关和命名隧道，前端隧道不变。

Two processes are expected: Python serves `/mcp` on loopback; cloudflared forwards
the public hostname to it. Both run without a console window. Process state and
logs live in `data/mcp_remote/`. The manager verifies checkout, PID creation time,
and command markers before stopping anything. Unrecorded services are never adopted.
两个进程分别负责协议和公网转发，不是重复隧道。不会接管未记录的进程。

Install `minimal-requirements.txt`, including `PyJWT[crypto]`, then configure
the new `mcp_remote` section in `config.json` or in App Settings:

```json
{
  "mcp_remote": {
    "enabled": true,
    "port": 9742,
    "public_hostname": "pan-mcp.example.com",
    "access_issuer": "https://your-team.cloudflareaccess.com",
    "access_audience": "YOUR_ACCESS_APPLICATION_AUD",
    "config_path": "data/mcp_remote/tunnel-source.yml",
    "binary_path": ""
  }
}
```

Use an existing dedicated named tunnel configuration whose ingress targets
`http://127.0.0.1:9742`, with credentials stored outside version control.
Cloudflare Access must protect the hostname, provide Managed OAuth with the
appropriate client callbacks, and allow only the intended user's identity.
The origin validates Access JWT signatures, issuer, audience, and expiration.
Pan does not provision DNS, Access policies or billing, and never needs the
Cloudflare management API token in its configuration.

使用专用隧道配置，入口转发到上述端口。保留 Cloudflare Access OAuth 和邮箱
策略，不要把管理 token 写进 Pan。GUI 的“随 Pan 启动”只在下一次 Pan 启动时
生效；保存配置不会自动终止服务，点击启动/重启应用新配置。启动失败请查看
`gateway.log` 和 `tunnel.log`。隧道进程存活不等于公网连接或 Chat 调用成功。

## Migration from external scheduled tasks / 从任务计划迁入

Keep the working external gateway/tunnel running while preparing the configuration.
On the next authorized maintenance window, stop and disable only the dedicated
MCP scheduled tasks, then start the pair from Pan. The manager deliberately refuses
to take over an occupied gateway port. Preserve the public URL and Access AUD so
existing ChatGPT connections retain their settings. Do not stop Pan or its frontend
tunnel to perform this migration.

当前服务未迁移前，GUI 可能显示未管理/已停止；这是所有权状态，不代表外部任务
运行的连接不可用。迁移时先停用专用 MCP 任务，再由 Pan 启动，避免两个管理者互相竞争。

On Pan launcher shutdown the owned pair is stopped; enabled pairs start on launcher
startup. Unexpected child exits are visible in status and can be restarted from the UI.
There is no automatic child watchdog or Windows login trigger in this integration.
Pan 退出后 ChatGPT 将无法使用 MCP，除非重新启动 Pan。
