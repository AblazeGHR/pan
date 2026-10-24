# Profile-wide Authorization Agent matrix

This profile has one human user and one global set of delegated Agent grants.
There is no Session/Agent/scope ACL in this feature. User identity and delegation
are separate: a valid in-memory UserAuth Bearer can manage the four objects even
when every Agent grant is off. Only the user can edit the grant matrix/password.

## State and API

`automation/authorization.json` contains `{revision: integer, permissions: {...}}`.
The missing-file default is revision 0 with all 22 booleans false. Unknown keys,
non-booleans and unreadable/corrupt state fail closed. Writes use atomic replace,
fsync and the existing UserAuth writer lock; a stale CAS fails without mutation.
No database/data migration is introduced. Password and Bearer behavior is retained.

- `GET /api/automation/authorization`: user Bearer; password `revision`,
  `authorizationRevision`, `permissions`, `configured` (no credential value).
- `PUT /api/automation/authorization/permissions`: user Bearer;
  `{expectedRevision, permissions}`; returns `{revision, permissions}`.
- The existing password PUT retains its separate password revision and revokes
  Bearer sessions on successful replacement. Grant changes do not log a credential
  or require logging out. GUI Authorization has the global Agent checkbox table.

| Object | Agent grants | Runtime/operation meaning |
|---|---|---|
| Trigger | read/create/updateConfig/lifecycle/delete | lifecycle enables/disables only; matching a Trigger never supplies effect authority |
| Action | read/create/updateConfig/lifecycle/delete + manageSystemActions | ordinary definition CRUD; dedicated lifecycle state; system definitions remain product-owned |
| Job Template | read/create/updateConfig/use/delete | passive complete configuration; no top-level enabled, lifecycle, execute |
| Action-reference Job | read/create/updateConfig/lifecycle/execute/delete | lifecycle includes enable/disable/pause/resume/cancel; execute includes run-now/retry/confirm-recovery |

A combined config+enabled update needs both grants. A pure lifecycle update does
not substitute for updateConfig. Config covers Action reference/arguments,
Trigger, Recovery, Overlap, Completion and Notification. A direct one-off Job
needs no Template and exposes `templateId:null`. Template `use` also needs Job
create and, if its copied Job config is enabled, Job lifecycle. It does not
execute a Template. The copied Job still needs execution authority at dispatch.

## Trusted transport and MCP

`packages/plugins/ipc.py` already owns the local Named Pipe (Windows) / Unix socket
bridge and publishes its process-local address via `PAN_PLUGIN_IPC`. This feature
reuses that bridge; no new network listener, HTTP identity header or password
injection is introduced. Both direct core stdio and native/core projections use
it. Bound Sessions retain the plugin runtime's existing core-enabled check.
Unbound local coordinators use the same profile grant via the private IPC address;
they do not gain a user principal. This is the existing same-OS-user IPC capability
boundary, not an OS sandbox against arbitrary code running as that OS user.

The `authorization_objects` bridge operation has an explicit four-object path and
method allowlist. It dispatches in-process to existing ASGI routes with a private
object-identity scope marker that HTTP cannot serialize. Bodies/headers claiming
Agent/Session/IPC identity are ignored as authorization. The matrix/password API,
groups, legacy background operations and arbitrary URLs are not bridge targets.
Existing group operations receive no new Agent permission policy; their anonymous
responses no longer disclose protected Action definitions.

The new core MCP `authorization_objects(kind, operation, item?, item_id?,
expected_revision?, idempotency_key?)` maps only the four-object operations above.
Legacy `action_drafts` and `job_config_drafts` retain their draft-only behavior,
but their four-object calls reach the same trusted IPC/grant boundary. Other Pan
MCP tools retain their existing transport and authorization. A raw/unbound HTTP
request is not promoted to MCP by any caller-supplied field. Missing private IPC
and unauthenticated non-stdio core transports fail closed for these objects.
Existing authenticated remote gateways can only use the same host-inherited IPC;
no OAuth/private exporter implementation or remote credentials were changed.

## Lifecycle, immutability and revocation

- Action lifecycle: `POST /api/actions/actions/{id}/lifecycle` with
  `{enabled,expectedRevision}` (Action registry CAS). System lifecycle additionally
  needs manageSystemActions for Agent calls. Its override is stored separately in
  `actionLifecycle`; the canonical product definition is never rewritten. All
  definition PUT/create/delete paths retain the product-managed rejection, even
  with every grant enabled.
- Trigger lifecycle: `POST /api/automation/triggers/{id}/lifecycle` with Trigger CAS.
- Job lifecycle: protected `action-jobs/{id}/lifecycle`, `/pause`, `/resume`, `/cancel`
  use configRevision. Existing cancel semantics remain explicit cancellation;
  grant withdrawal or Action disable does not call cancel on a started Attempt.
- Template top-level enabled is rejected on new writes. Historical enabled fields
  are ignored by read-only passive projection; no automatic record rewrite occurs.
- Job/Trigger records persist a server-derived `authorization` basis (user or
  agent). Run/Attempt admission persists the current authorization revision.
  Trusted Agent enable/config/resume is not relabelled as user authorization.
  Explicit user execution can authorize a run independently of Agent grants.
- Delegated execution needs `job.execute`; delegated system effects additionally
  need manageSystemActions. Template delegation also checks use/create/lifecycle
  as applicable. New dispatch, retry/recovery and each dynamic effect callback
  re-read current grants/revision and current Action enablement. User-authorized
  execution is not globally stopped by withdrawal of Agent grants. Failed or
  unknown effects are never auto-replayed to recover authorization.
- Action disable blocks new Jobs/Template copies and new Attempts/recovery. Pending
  Trigger Runs fail without claiming another Attempt. Current provider effects
  are allowed to finish; later sub-effects are independently rechecked. Notice
  dispatch for delegated Action Jobs also respects current execution grants.
- Owner/generation/lease/Pause/Illegal, business revision and receipt/uncertainty
  gates remain in the existing execution paths. Legacy background, message,
  broadcast, Session/Worker/Queue permissions are not added to the matrix.

Aliases (`/api/jobs` and scheduler reads) do not reveal Action-reference Jobs to
anonymous HTTP: list/preview omit those records, detail/runs require the user
principal. Their other Job kinds retain existing behavior. Legacy mutation aliases
still refuse Action-reference Jobs and direct clients to the protected API.
Canonical Action Job WS projections include structured source/target and unified
config; reads never emit raw Job mutation events. This preserves editor CAS and
an opened dirty draft across concurrent WS updates.

## Validation and fixture migration

See `artifacts/authorization-candidate/validation.md` for exact commands, counts,
ports and remaining verification boundaries. No new skips or relaxed business
assertions were added. User regression clients explicitly log into their owned
profile; raw/forged/expired HTTP cases still omit credentials and assert denial.

- `test_action_groups`: owned ASGI user client, default Agent grants remain off;
  running blockers, idle detach, rollback, pin/lease and I/O assertions retained.
- `test_automation_core`: disabled draft management now has a user Bearer;
  self-reported agentId does not change the principal. Expiry/CAS/401 assertions stay.
- `test_job_action_templates`, `test_job_mcp_config`, Template editor tests:
  remove Template enable controls/fixtures; keep complete-copy/idempotency,
  revision, reference integrity and explicit repair checks. Config.enabled still
  controls only the copied Job. Tests explicitly reject Template lifecycle fields.
- Action-disabled retry tests keep failed terminal status, no extra effect and
  original error category; the attempt-count assertion is stricter (one original
  Attempt, no second Attempt admitted) and stale retry intent is removed.
- Owned Automation/Job/EventStore/Plugin/ETA HTTP fixtures attach their own user
  principal through a shared fixture helper. Explicit raw denial requests remain
  unauthenticated. Browser API requests likewise use a separate owned user Bearer.
- Chromium retains production selectors, actual HTTP/WS, dirty editor, concurrent
  CAS, fixed Template copy and responsive geometry assertions. Its removed enable
  checkbox assertion is replaced with an absence assertion for passive Templates.
- Error serialization no longer copies command stderr or invalid argument values
  into Attempt errors. Configuration/result data remains in its existing stores;
  authorization adds no sensitive parameter, password or Bearer logging.
