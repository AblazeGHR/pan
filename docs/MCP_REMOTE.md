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
python scripts/run_tests_isolated.py -- python -m pytest tests/test_mcp_remote_parity.py tests/test_mcp_remote_runtime.py -o addopts= -q --junitxml=artifacts/remote-parity.xml
```

The parity test starts the real gateway on an ephemeral loopback port (never
8767/8768/9742) with a locally generated Access key, then compares remote
`tools/list` descriptions and input schemas for every tool with stdio. It also
checks the current history/model/report/Action/Job schemas, force transaction IDs,
unknown-result non-replay, invalid Access claims, and unbound report/queue access.
The inherited local caller is deliberately seeded before gateway startup and must
be cleared. Owned port/PID evidence is written to `artifacts/remote-http-owned.json`;
the listener must close at teardown. Install test dependencies before running; the
command does not install or update shared dependencies. It is not a production or public-endpoint test.

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

## Core authorization and transport policy / Core 授权与传输策略

At baseline `d5cd05e48da3b43727f6a4e8108adb4dda1bf49b`, real Gateway +
Core tests reproduced `403 / Bound Core Session required` for `session_list`
and `session_get`, while `tools/list` succeeded. Before `1e531c2f`, ordinary
calls used uncredentialed HTTP with an unbound external coordinator; that
commit moved them to bound local IPC and explicitly deferred remote authority.
On 2026-11-28 the user authorized restoring the existing remote read/write
uses across all Sessions, without granting a local Session or administrator
identity. This is an application-wide coordinator scope: every identity
accepted by the configured Access application has the same ordinary Session
scope. The Cloudflare Access policy must limit who can enter that application.

The Gateway now binds the verified JWT only to the current request, resets it
on success/error, and forwards it over the existing same-user Core IPC as
`remote_http`. Core rechecks the signature, issuer, audience and expiry against
its own current `mcp_remote` settings on every operation. The Gateway cannot
select that policy or supply a Session identity. Core reuses the ordinary
machine IPC route allowlist and original business handlers, excluding bound
Session and protected object operations. It never sets caller headers, a user
Bearer, or a delegated Agent authorization marker for a remote request.
An IPC timeout remains `unknown`, `retryable:false`; it is never replayed.

| Tool family | Remote transport policy |
| --- | --- |
| `session_list`, `agent_list`, `worker_list`; `session_get`, `session_message_get`, `session_history_search`, `session_history`, `session_usage` | Ordinary Core reads across Sessions. `session_usage` requires an explicit target outside stdio. Real list/get acceptance covers the authenticated dispatch path. |
| `system_fields`, `model_catalog`, `model_list`, `codex_quota`, `workspace_list` | Ordinary metadata reads; existing input and target checks remain. |
| Session create/import/update/delete/batch-delete/handoff; Worker spawn/task/assign/send/force/notify/kill/delayed restart and aliases; workspace create/delete | Existing ordinary read/write paths. Original validation, ownership, pause, readonly, capability and transaction rules remain; sourceSessionId and a local Agent prefix are never invented. Real acceptance covers settings persistence, a paused force-send queue receipt, and delayed-restart rejection without a live Worker. |
| `agent_background_*`, `agent_message_job_*` | Existing scoped Job paths. Creation needs an explicit target outside stdio; original receipts and unknown-result non-replay remain. |
| `scheduler_*`, `session_managed`, `manager_chain`, claim/unclaim/readonly, report subscriptions, QQ subscriptions, notification/reminder/inbox tools, `permission_prompt` | Existing caller-dependent operations remain unavailable without a bound Session; Access does not supply one. `agent_notify` belongs to the ordinary write row above. |
| `terminal_*`, `self_queue_*`, `report_status`, `report_consume`, `reminding_card`, `locker_message`, `interaction_card` | Bound local callers only. Remote producer calls explicitly reject before accessing a local caller's state; card get/receipt also require a bound caller. |
| `authorization_objects`, Action/Job drafts | Protected local object transport and the user's delegated Agent grants remain separate. Remote Access is not an Agent grant and does not enter these paths. |
| `pan_handbook` | Reads the checkout's handbook directly; does not dispatch a Core operation. |

One shared registry is preserved, including local-only metadata for compatibility.
Catalog parity is not a promise that a local-only tool is remotely callable; this
matrix documents the existing transport distinction. No remote-only tool copy
or schema was introduced. Families without individual real-call coverage above
are described by source/route policy, not claimed as fully exercised providers.

For deployment, both Core and Gateway must run the candidate from the same
checkout. The Gateway reads that checkout's `data/core-ipc.json` locator at
startup, ignoring an inherited `PAN_PLUGIN_IPC` from another instance. Configure
the same Access issuer, audience and hostname in Core's `mcp_remote` settings;
there is no new password, administrator token or Cloudflare management token.
The `enabled` field continues to control managed startup, not JWT permissions.
Restart the owned Gateway after replacing Core so it reads the current locator;
missing/stale IPC fails closed, without falling back to localhost HTTP. Deployment,
real Cloudflare configuration and existing services are separate operator steps.

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
