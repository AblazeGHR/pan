# MCP remote connection / MCP 远程连接

Pan can manage an independent authenticated MCP gateway and a named Cloudflare tunnel.
应用设置中的 **MCP 远程连接** 管理独立网关和命名隧道，前端隧道不变。

Two processes are expected: Python serves `/mcp` on loopback; cloudflared forwards
the public hostname to it. Both run without a console window. Process state and
logs live in `data/mcp_remote/`. The manager verifies checkout, PID creation time,
and command markers before stopping anything. Unrecorded services are never adopted.
两个进程分别负责协议和公网转发，不是重复隧道。不会接管未记录的进程。

## Source and single implementation / 源码与单一实现

- **Entry**: `packages/remote/mcp_gateway.py` is the remote ASGI transport and
  Cloudflare Access guard. It contains no tools, input schemas or tool
  docstrings.
- **Lifecycle**: `packages/remote/mcp_runtime.py` owns the gateway/tunnel pair.
  `packages/core/launcher.py` calls `control(root, "start")` after Pan Core is
  ready when `mcp_remote.enabled` is true ("随 Pan 启动" / start with Pan).
- **Single registry**: `create_app()` imports `packages.mcp.server.mcp` from
  the checkout named by `pan_project_root` and serves that FastMCP registry over
  streamable HTTP. Local stdio and remote clients therefore see the same tool
  names, descriptions and JSON schemas; a gateway restart reloads the checkout's
  current source. There is no second remote package to update.
- **Address**: the public endpoint is `https://<public_hostname>/mcp`; the
  tunnel ingress forwards the hostname to `http://127.0.0.1:<port>` (default
  `9742`). Access JWT validation is unchanged and required before MCP framing.
- **Client cache**: Codex Apps may cache tool metadata. Reconnect or refresh the
  connector after a deployment; a server-side description change alone cannot
  invalidate a client's cached copy.
- **Why no separate mechanism**: the transport already reads the stdio registry
  at process start, so no sync step or duplicated registry is needed. Do not add
  remote-only tool copies; `tests/test_mcp_remote_parity.py` locks the invariant.

验证 / Verification:

```powershell
python scripts/run_tests_isolated.py -- uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout -- python -m pytest tests/test_mcp_remote_parity.py tests/test_mcp_remote_runtime.py -o addopts= -q
```

The parity test starts the real gateway on an ephemeral loopback port (never
8767/8768/9742) with a locally generated Access key, then compares remote
`tools/list` with stdio and checks `agent_send_force` description/schema/call
semantics. It is not a production or public-endpoint test.

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

Rollback / 回滚：保留原外部任务使用的目录、任务定义和隧道配置，直到 Pan 托管的
一对进程经客户端实测通过。回滚时先在应用设置中停止 Pan 托管的一对进程，确认端口
没有被残留监听者占用，再重新启用原任务；不要让两个管理者同时绑定同一端口。Pan
只会停止自己记录且身份校验通过的进程，不会接管或删除原目录。

On Pan launcher shutdown the owned pair is stopped; enabled pairs start on launcher
startup. Unexpected child exits are visible in status and can be restarted from the UI.
There is no automatic child watchdog or Windows login trigger in this integration.
Pan 退出后 ChatGPT 将无法使用 MCP，除非重新启动 Pan。
