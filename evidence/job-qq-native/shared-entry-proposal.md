# Shared-entry proposal (MA integration only)

Do not modify shared files in this TA tree. MA may compose this exact wiring:

```python
from packages.actions.qq_channel_provider import QQChannelProvider
from packages.web import server as qq_server

executor = TypedExecutor(
    session_provider=WorkerSessionProvider(),
    channel_provider=QQChannelProvider(qq_server._send_scheduled_qq),
)
```

The canonical `channel` Action already supplies `targetType`, `targetId`, `text`,
and optional `botUin`; the provider maps `user/group` to the existing
`private/group` transport names and calls `_send_scheduled_qq`. No endpoint,
transport, receipt query, retry, ledger, or SQLite state is added. Existing
`effectKey` persistence and unknown/no-replay behavior remain owned by the
canonical executor/host. MA must verify import ownership and choose the
existing trusted configuration boundary before composing this wiring.
