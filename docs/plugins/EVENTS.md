# External plugin events (pan-events/v1)

Events are an optional `events` declaration in an ordinary `pan-plugin/v1`
manifest. Catalog validates it alongside tool sources, Panels, and exposure.
There is no additional install/enable flag and no public event publication API.
See [the runnable Node example](../../examples/event-plugin/manifest.json) and
[provider implementation](../../examples/event-plugin/provider.mjs).

## Declarations and identity

```json
{
  "events": {
    "provider": {"transport":"stdio_jsonrpc", "command_ref":"my-process"},
    "definitions": [{
      "type":"my-plugin.changed", "schemaVersion":1,
      "description":"A domain object changed.",
      "schema":{"type":"object","properties":{"value":{"type":"integer"}},
                "required":["value"],"additionalProperties":false}
    }],
    "subscriptions":["workers.all_idle", "another-plugin.changed"]
  }
}
```

`command_ref` refers to a configured local stdio command in `mcp_servers`, with
the existing command/argument/env interpolation. Remote HTTP/SSE and Pan's own
MCP projection are not event transports in this version. The plugin still needs
its ordinary `source` (`mcp` or `pan_api`). Event registration uses the manifest,
not declarations submitted by an unbound client. IDs use `<plugin-id>.` namespace;
schemas use JSON Schema 2020-12 without references. Version is a positive integer;
description is required. Subscriptions are an OR list of exact type IDs; empty
is allowed, including for a pure publisher. Empty definitions allow a pure
subscriber. There are no wildcard, historical, or competing-consumer semantics.

The host assigns identity from the Catalog plugin and its owned connection.
`source` in publication parameters is rejected, and publishing an event outside
the connection's definitions fails. A generation is a correlation fence within
that connection, not a transferable authorization token. No HTTP/IPC operation
accepts an event handle, registers a runtime owner, or grants publish rights.
Descriptions are presentation metadata on the registered Bus definitions;
Bus schema, owner, and availability remain authoritative.

## Shared lifecycle

The generic plugin Runtime owns one service-wide **event-role** process for a
plugin, separate from its existing per-Session/per-Panel tool providers. It starts
when at least one persisted Session binding enables that plugin, including before
any Worker/tool call. `standalone`, `pan_native`, and `gateway` all refer to this
same event process. Creating or closing MCP projections cannot acquire/revoke an
event owner. Panels do not create synthetic event owners. No Action Group-specific
event service is introduced; Group immutable releases, pins, build/unbuild,
and Worker deferred restart semantics remain unchanged.

Bindings are the existing authority (`plugin_bindings`, or the legacy MCP list).
The runtime keeps a membership map of Session ID and plugin-selection revision.
Removal of one Session keeps a peer while another unchanged member still owns it.
Removal of the last member revokes its opaque Bus handle, cancels subscriptions,
retires the owned process tree, and marks definitions unavailable. Replacing the
last membership or changing the event declaration/command creates a new generation.
An unrelated selection edit may conservatively reconnect a sole member.

Web Session mutations reconcile binding intent and complete trusted Bus revocation
before their response is returned. Reconciliation does not await external startup,
handshakes, process exit, or stderr. One independent lifecycle task per plugin
coalesces its latest intent; no global lock spans peer work. Selection responses
therefore do not imply that a new external peer is already ready. Observe the
Automation definitions/diagnostics or the example's ready record for readiness.
Publication
and delivery recheck current bindings/revisions/command digest at admission, so a
connection cannot publish during the reconciliation delay. Other internal binding
changes and external manifest edits converge through the generic plugin event
watcher (2 seconds, plus bounded connection work). A disable/enable cycle with a
changed selection revision fences the old peer even if the watcher missed the
intermediate disabled snapshot. Editing configuration files outside Pan without
updating revisions or observing the intermediate state has no transactional
disable/enable guarantee.

An invalidated connection is fenced synchronously, and binding reconciliation
joins only local Bus revocation before returning. Publish/deliver also recheck
identity after their off-loop snapshot read. Stale snapshots cannot overtake a
newer reconciliation, and the runtime's terminal latch blocks all later startup
and readiness. A per-plugin replacement waits for that plugin's old cleanup;
unrelated plugins continue independently.

EOF, malformed transport, reader cancellation, failed handshake, and delivery
timeout start the connection's own cleanup immediately, without waiting for the
2-second watcher. Cleanup kills the owned tree, revokes the old handle, joins
cancelled startup (retaining any process handle created during cancellation),
and cancels/joins pipe tasks with a bounded deadline. Still-enabled peers reconnect
on the next lifecycle reconciliation. Full
Pan startup restores event peers from actual persisted bindings; shutdown closes
peers before Automation Service. Windows uses a kill-on-close Job established by
an owned stdio helper before external code starts; POSIX uses an owned process
group. Native provider code must still behave appropriately in its own environment;
this is process cleanup and same-user trust, not an OS security sandbox.

After handshake the adapter calls `Service.plugin_enabled(id)`, registers declared
types/subscriptions, then calls `plugin_ready()` to wake queued Runs. On loss it
calls `plugin_disabled(handle)`. Trigger definitions are retained. Unavailable
dependencies wait; actual re-registration and readiness wake the existing runner.
No queued Run or Action is replayed by this transport. Started/uncertain work
retains the Engine's own semantics.

## Wire protocol

UTF-8 JSON-RPC 2.0 objects, one object per newline, on stdin/stdout. Stdout must
contain only protocol frames; stderr is drained into bounded diagnostics. Both
directions require request IDs and responses. Host IDs use `host-N`; plugin IDs
should use a distinct prefix. No batching or notifications in this extension.

1. Host sends `provider.initialize` with
   `{protocol_version:"pan-capability/v1",extensions:["pan-events/v1"],role:"events"}`.
   Provider returns `{protocol_version:"pan-capability/v1",extensions:["pan-events/v1"]}`.
2. Host sends `events.initialize` with
   `{protocol_version:"pan-events/v1",plugin_id,generation,definitions,subscriptions}`.
   Provider returns **exactly** `{ready:true}` after initialization. It must not
   publish yet. Declarations are supplied by the host; this is their activation
   handshake, not an arbitrary registration channel.
3. Host registers the runtime with Service/Bus, resumes eligible queued work, then
   sends `events.ready` with `{generation}`. Provider acknowledges and may publish.
4. Provider sends `events.publish` with
   `{generation,type,payload,event_id?}`. Only these keys are allowed. Response is
   `{event,accepted:true,persisted:true,duplicate,rejected:[]}` after durable inbox
   commit, or JSON-RPC error `-32602` (including admission/disk/capacity failure). A reused
   event ID is deliberate redelivery, never automatic transport retry.
5. For matching interests, host sends `events.deliver` with `{generation,event}`.
   Provider returns **exactly** `{accepted:true}`. Independent subscribers receive
   separate copies of the same envelope/ID. Rejection/timeout is diagnostic and
   disconnects this subscriber; it does not block other consumers.

`accepted` from publish means bounded Bus admission, not durable TriggerRun or
Action completion. Partial fanout is possible and reported in `rejected`.
Subscriber acknowledgement is only realtime receipt; there is no durable inbox,
delivery retry, cursor, replay, or catch-up. Read Automation Run state to observe
execution. A result lost after an admitted request is unknown; do not blindly
replay side effects.

Limits: 128 KiB encoded frame including newline, 64 KiB payload (Bus limit), 128
items per subscriber, one delivery in flight per peer, 5-second handshake/write/
delivery response deadlines. One publication owner processes a bounded 128-item
queue sequentially; the reader continues resolving delivery responses during
durable admission waits. Full publication queues return an explicit error. No
per-frame task is created. Cleanup settles an in-flight durable commit before
retiring the publication owner; lost receipts remain unknown. Bus caps
owners/definitions/subscribers at 64/128/64;
Catalog caps each declaration/interest list at 128. Full queues reject visibly;
the last 100 Bus diagnostics include plugin, delivery, protocol, and startup
failures. Stderr is truncated per entry. Slow delivery retires only that peer.
Oversize/invalid framing terminates its connection; invalid publication fields,
identity, generation, schema, or ownership return errors without granting rights.

## Run the example

The example needs Node on PATH and no npm packages. Add its manifest absolute
path to your **own isolated** Pan `plugin_manifests`; do not replace a user's
configuration. Enable `producer` and `observer` through ordinary Session plugin
bindings. A second `producer` Session reuses its existing event peer. The
`producer.echo`/`observer.echo` tool capabilities also work through the three
normal MCP projections, on their separate existing tool-role processes.

The example writes `observed.jsonl` below the host-provided
`PAN_PLUGIN_DATA_DIR` (`<data>/plugins/<id>`), recording activation, generation,
publications, and delivery. Atomically write `publish.json` there containing:

```json
{"type":"producer.ping","payload":{"value":42},"event_id":"demo-1"}
```

The Node producer consumes that file and submits its own connection-bound event.
This file is a demonstration input to an external program, not a new Pan API.
Production plugins replace it with their domain observation. The host sets
`PAN_PLUGIN_ID`, `PAN_PLUGIN_ROLE=events`, and the existing plugin data directory;
it removes Session identity from event-role environment. Tool processes remain
Session-bound. If using one executable for both roles, implement both protocols
and start background event production only after `events.ready`.

Use the Automation user login to enable a Trigger for `producer.ping` and select
an enabled Action. Ordinary Agent draft and user-management rules are unchanged.
Do not publish secrets: realtime recipients get the full declared payload.

## Durable publication boundary

See [Event Store foundation](../design/Event-Trigger-Action-v0.2-event-store.md).
The adapter awaits `bus.publish_async` after its existing generation/binding
checks; revocation while waiting for admission is rejected. A committed historical
fact remains deliverable after its producer disconnects. Its frozen eligible
Trigger revisions are used, and unavailable dependencies wait for real readiness.
No source identity may be supplied through payload or the replay endpoint.

`persisted` acknowledges Event storage only. Delivery to Trigger matching is
at-least-once, deduplicated by retained Event+Trigger. Plugin `events.deliver`
subscriptions still provide live best-effort notifications: there is no durable
per-subscriber cursor, receipt, or historical subscription here. A publisher may
resend the same fact/ID if its response was lost. It must not assign an old ID to
a different fact or rely on dedup after retention expires. Replay never retries a
failed/cancelled/succeeded Run, and refuses uncertain Runs.
