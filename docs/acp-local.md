# Local text ACP (candidate)

ACP is an opt-in local capability, separate from browser login and plugin IPC.
On Windows, create a profile for an existing Session template and workspace:

```powershell
pan --root D:/path/to/checkout acp-init --profile editor --template default --workspace D:/path/to/workspace
```

Start or restart that Pan instance after provisioning. Configure an ACP client
to launch `pan` with arguments `--root`, the checkout path, `acp`, `--profile`,
and `editor`. The ACP process communicates using JSON-RPC on stdin/stdout.
Only the configured workspace and its descendants are allowed. Profile
credentials and configuration reside in `data/acp/<name>.profile`, protected
with user-scope DPAPI and owner-only Windows ACLs. Remove or rename that file
to revoke subsequent calls; there is no credential in command arguments.
The same Windows user remains the local trust boundary. The dedicated pipe
rejects remote clients using the existing Windows pipe implementation. No
equivalent other-host test is implied. Other operating systems currently fail
closed with an unsupported error.

Only text prompts are supported. A Pan Session is created on the first prompt;
each turn uses the normal durable user queue. ACP sees only server-side
projection for that Session and its matching queue/Worker execution. Polling
uses an output cursor, so a lost poll response can be retried without duplicating
the text. An uncertain submission or interrupt is never replayed. Queued
cancel uses the original queue item and running cancel currently requires a
Codex native turn identity; interrupt submission is not terminal confirmation.
The original matching terminal event is required for a completed response.

Session continuation across ACP connections, attachments, approval UI and
remote access are not implemented. The service persists protected ACP intent
identities before side effects. After a service restart it can reconcile the
original queue receipt to its matching persisted terminal. If the streamed
output was interrupted, it reports an error with the matching `panTerminal`
receipt rather than claiming that the client received the whole answer. A
missing or ambiguous receipt remains unknown; do not resubmit the prompt.
Recovery records contain identities and workspace configuration, not prompts
or stream text. Active or unknown records remain available for reconciliation;
they count against the bounded context limit.
