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

Text and local `resource_link` prompts are supported. Links must resolve to an
existing non-reparse file within an authorized workspace root; network URIs,
UNC paths and out-of-scope paths are rejected. The Worker receives the local
path as text and this layer does not open or fetch the file. A Pan Session is created on the first prompt;
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
or stream text. Disconnect detaches an uncertain turn from the in-memory
capacity while preserving its recovery record. If a client disappears without
disconnecting, later admissions reap an inactive context after 60 seconds;
an in-flight submission remains protected until it returns. Recovery loads
the original protected record on demand and never resubmits the prompt.
Each connection-local context retains at most 1,024 completed turn identities;
after that it rejects further prompts so a late cancel cannot be mistaken for
a new turn. Open a new ACP session to continue.

To authorize a stdio MCP manifest entry when creating a profile, add
`--allow-mcp MANIFEST_ID` to `acp-init`. This pins the existing manifest's
physical descriptor in the protected profile. A client descriptor must match
the name, absolute command, arguments and empty environment exactly; a changed
manifest is rejected, including on a later Worker startup. Commands and
arguments must not contain secrets. Client MCP selections are currently
limited to CBC, Claude and Codex templates; real-provider MCP startup has not
been verified by the synthetic fixture. Nonempty environments remain unsupported:
the existing Codex adapter serializes MCP environment values into process
arguments, so accepting them would leak those values. Do not put credentials in
the manifest or arguments. This subset is not fully ACP v1 conformant; the
unfiltered TCK uses an out-of-scope nonexistent file and an unapproved MCP
command and is expected to reject them. Permission requests are not exercised.
