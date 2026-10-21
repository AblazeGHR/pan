# Source-aware sends and automatic answer reports

This feature stays on `feature/source-aware-auto-report-20261019` based on
`ba51b72ec95a08e9c64fd289f189d46746033000`. It is excluded from the current
0.3.4 release. TA completion is not MA acceptance; integration into main/practical
must wait for the actual release to finish and subsequent MA acceptance.

## Explicit agent messages

`assign`, `send`, force send, their worker aliases, and the corresponding HTTP
task/send/assign routes classify agent messages from the validated sending
Session and its persisted `managed_by` chain. A resolved ancestor is a Report
recipient; self, descendants, siblings, unrelated or unmanaged recipients retain
MA (`agent`) provenance. Text markers never establish identity. A missing parent
ends traversal; only already resolved ancestors count. Any cycle invalidates
ancestor classification for the entire traversal, falling back to MA.

The original task envelope remains the delivery transaction, with `source=report`
and `explicitReport=true` for explicit upstream sends. This preserves the complete
body/parts, queue ID, formal assign idempotency, inherited task ID, force capability
selection and accepted/rejected/unknown semantics. These rows use the existing
Report pause predicate, including Stop reports locks at enqueue and later pause
changes. Queue receipts and history retain Report provenance; navigation, source
tags and report visibility recognize that provenance even without a text header.
An existing MA text header does not hide a Report when only MA visibility is off.

Classification does not claim, subscribe or alter access. MCP's existing access
check still runs before sending; HTTP retains its existing local trust boundary.
Report classification alone does not deliver an automatic completion report;
the manager subscription rules remain independently required.

## Automatic answer policy

Global persisted configuration is `ui.autoReport`:

```json
{"system": false, "ma": true, "user": false, "plugins": {}}
```

System includes structured background Job terminal notices and automation
inputs. MA includes agent/meta-agent inputs. Real user includes dashboard inputs
and explicitly admitted native TUI turns. Internal system-prompt bootstrap and
unknown origins do not enable automatic answer reporting. Report inputs never
enable it, including explicit upstream task envelopes and automatic report
batches. Manual sends/reports, ordinary answer publication, logs, terminal
records and infrastructure zombie reports are independent of these switches.

The policy decision is frozen at queue reservation before provider handoff,
not at enqueue and not from history or provider `role=user`. It is persisted per
input as `autoReportAdmission`, in the delivery ledger, input receipts, active
offline-run record and terminal result. Requeued/recovered reserved inputs reuse
their original admission decision. Previously queued inputs without a decision
use the policy at their first reservation. Already delivered inputs are not
replayed just to recover a policy decision. A mixed batch enables an automatic
answer report if any non-Report input's frozen policy enables it.

Accepted native Steer adds its input's frozen decision to the same execution.
Rejected or unknown delivery does not enable a report. A late acceptance can
settle the original delivery transaction but cannot attach its decision to a
different execution or backdate an already terminal result. Terminal callbacks
carry a copy of the execution decision through awaits and cleanup. Later queue,
history, manifest or settings changes cannot reclassify that terminal.

Automatic answers still require a current subscribed manager. Replying to a
Report therefore produces ordinary output and a terminal result, but no further
automatic manager report. This prevents Report-answer feedback even with every
Core switch enabled or corrupt management cycles.

## Plugin contract

Registering tools or panels does not declare a message source. A plugin opts
in independently with an optional `message_source` object:

```json
{
  "protocol": "pan-plugin/v1",
  "id": "example",
  "source": {"kind": "mcp", "server_ref": "example-provider"},
  "message_source": {"auto_report_default": true, "legacy_sources": ["old-example"]}
}
```

`auto_report_default` is optional and defaults to false. `message_source: {}`
declares a message-capable plugin with a false default. An omitted
`message_source` declares no message capability. Legacy source aliases must be
unique and cannot shadow Core origins. QQ declares true and the legacy `qq`
source explicitly, including the built-in legacy QQ descriptor.

`GET /api/plugins/message-sources` returns registered source IDs, display names,
declaration defaults and effective values. It does not depend on panels or their
count. Behavior renders one independent switch per returned source, with no
plugin master switch. `ui.autoReport.plugins[pluginId]` is an explicit user
override, taking precedence over the plugin declaration. Reset removes all
overrides and restores Core defaults; each plugin then uses its declaration or
false fallback. Missing/legacy config uses the same defaults without rewriting
the file on read. Incorrect settings writes return 422 before saving.

Plugin producers can call `worker.enqueue_plugin_message(plugin_id, session_id,
text, client_message_id=...)` or `POST /api/plugins/{plugin_id}/messages` with
`sessionId`, `text`, and optional `clientMessageId`. Ingress requires the declared
message capability and writes `source=plugin`, canonical `sourcePluginId` and
the original durable task identity. It uses normal queue gates and recovery;
it grants no additional access capabilities. Existing QQ/channel reminder
transport remains intact and gains explicit plugin metadata. Old QQ envelopes
are recognized by their declared legacy source, independently of message text.

`PUT /api/settings/ui` accepts a full `autoReport` value; omitted members inside
that value use defaults and an empty `plugins` map removes overrides. Other UI
settings retain their existing update behavior. GET sanitizes malformed persisted
values, while PUT strictly rejects unknown fields and non-boolean values.
