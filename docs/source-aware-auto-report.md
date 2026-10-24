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
{"system": false, "ma": true, "user": false, "plugins": {},
 "summary": {"system": false, "ma": false, "user": false, "plugins": {}}}
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

## Prompt reference on reports

Every automatic answer report, the durable terminal row and `lastResult` carry
two prompt fields. Both keys are always present; both are `null` when the input
identity cannot be proven:

- `promptMessageId`: the canonical Pan history `messageId` of the input this
  execution actually admitted.
- `promptSummary`: the first 100 Unicode characters of the canonical body that
  `promptMessageId` resolves to, only when the prompt-summary policy for that
  input's structured origin is enabled (and, for a merged batch, for every
  contributor).

Identity rules:

- The canonical id is assigned by `session.append_history` when the input row is
  committed at the successful provider hand-off, or when an accepted Steer row
  is appended. Pan never derives it from the queue transaction id. A queue row's
  `queueItemId`/`id` remains the durable delivery key; `delivered_keys` links it
  to the history row, and only the history `messageId` is reported.
- The reference is frozen at that admission moment on the Worker, then copied
  into the terminal record before any terminal await. A row queued but not yet
  executed, a late HTTP enqueue, a later history append or a provider frame
  cannot supply or replace it, so turns cannot borrow each other's inputs.
- Ordinary send, Force send, automatic Force, plugin ingress, Job/automation
  notices and accepted Steers all use the same rule. Identity comes from the
  structured `source`/`sourceSessionId`/`sourcePluginId`/`noticeKind` fields,
  never from `role=user` or body markers.
- A merged report/channel batch is one provider-visible input message and
  therefore has one canonical `messageId` shared by its members. Its
  `promptSummary` is the prefix of that same canonical row content, and it is
  attached only when **every** contributing item's frozen decision explicitly
  enabled summaries. If any contributor is disabled, unknown or a Report, the
  summary is `null`: one enabled source must never preview a body that another
  source contributed to the same provider-visible message.
- Single inputs use the same rule: the summary is taken from the canonical
  history row (task body, or the formatted notice/report batch text), not from
  a parallel raw field. Looking up the reported `messageId` therefore always
  returns a body whose first 100 characters equal `promptSummary`.
- An accepted Steer replaces this turn's reference with the Steer input row.
  Rejected or unknown receipts, and late acceptances for another Worker,
  generation or execution, never count as admission and never change it.
- A recovered hand-off may reuse an existing history row only when that one row
  carries **every** member's delivery key; a partial match belongs to a
  different/partial batch and yields no reference instead of borrowing its id.
- No id is invented when evidence is missing: native-TUI admission, preflight
  failures before hand-off, zombie reports for a run that never handed off,
  legacy terminal/report rows and legacy `autoReportAdmission` decisions all
  keep `null` (or the field absent on old data, which reads as `null` and is
  never rewritten). Turning the summary switch on later never backfills an old
  report.

The summary policy is independent of whether a report is sent:

- `ui.autoReport.summary` has the same four origins (system/ma/user/plugin) as
  answer reporting. Every switch defaults to false, and a plugin override never
  inherits `message_source.auto_report_default`.
- The excerpt is a verbatim prefix of the canonical history body its
  `messageId` resolves to, and carries no model call, no suffix and no notice
  header of its own. With the switch off for any contributing input, both the
  Worker and the report store `promptSummary: null`; the input body never
  leaves the Session with the report.
- `GET /api/plugins/message-sources` adds `effectiveSummaryEnabled` (explicit
  override or false). `PUT /api/settings/ui` validates `summary` strictly and
  keeps omitted members at the all-false defaults.

Prompt fields follow the existing delivery boundaries: subscription report
items, manual explicit reports resolved from the same terminal row, terminal
diagnostics, report dedupe/unknown-no-retry, original queue ids and
owner/generation checks are otherwise unchanged.

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

## 2026-10-21 integration with current main

The original branch and its evidence above remain historical, unchanged. The
integration candidate starts at main `3f601e9c66125d7a0313e4b679958bdc49eac935`,
merges the complete original feature history, and additionally includes main
`b6502ea940b3d852b7e6824f6c2d4e66fe7666fc`. This candidate is delivered for MA
acceptance; it does not move main/practical or authorize service operations.

The current Illegal/Wake admission writer remains authoritative for every new
message, including plugin messages and explicit upstream Report tasks. Ordinary
Force and automatic Force cannot implicitly Wake or clear the incident's locks.
Auto Force's Report switch recognizes explicit upstream task messages, while
formal assign remains a dispatch, excluded from automatic injection. Auto Force
settings and automatic-answer-report settings remain independent.

Own-queue reads hide every `source=report` task's body/parts, even without the
optional `explicitReport` marker. `report_consume` handles such a task as a
single original unit, preserving its text, attachment projection, task ID and
formal idempotency. It never combines task envelopes with `result` report
envelopes. Ordinary completion reports retain main's batch/single-control
behavior. A corrupt restored mixed unit is rejected instead of dropping input.
Original-ID queries remain metadata-only, and unknown receipts never regroup
or retransmit the unit.

Registry/manifest discovery runs through cancellation-safe off-loop IO. Action
Session/Group reservations remain held until admitted IO/writes retire. Policy
metadata is computed on detached candidates and published through the existing
durable reservation; failed writes restore item/ledger policy and do not update
the Worker decision. A late accepted receipt can settle the original ID but can
only extend the active decision when Worker identity, generation and execution
all still match. Native TUI admission snapshots real-user policy in its durable
offline run. Runtime handoff/preflight errors and zombie infrastructure reports
retain their existing reporting/subscription rules independently of answer
policy.

New integration commands, JUnit, production browser evidence and limits are in
`evidence/source-aware-main-integration-20261021/README.md`.
