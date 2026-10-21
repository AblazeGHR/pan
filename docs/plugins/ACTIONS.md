# Actions and Group MCP plugins

## Create and organize

The Panel has three sections: **Actions** lists definitions and their edit entry;
**New Action** creates an API or command definition; **Groups** creates/edits shared
selections, builds releases and offers **Use in Session**. That entry loads the
chosen Session and uses the shared PluginSelection component and existing three
modes. An empty Session list explains how to create a Session and refresh.

IDs are assigned by the server when omitted, independently of display names.
The Panel hides ID inputs on creation; existing items expose a selectable ID and
Copy ID under Details. Rename preserves the ID, shared references and plugin/release
identity. No existing data is migrated and no import feature is added.

`POST /api/actions/actions` and `POST /api/actions/groups` accept
`{item: {...}, expectedRevision?: number}`. Omit `item.id` for automatic allocation;
the response includes the registry plus `item` with the authoritative ID. Explicit
valid IDs are accepted on creation, but an existing ID is rejected without overwrite.
Existing `PUT /api/actions/{kind}/{id}` remains compatible and requires the matching
item ID. Agent draft `put` also permits omitted `item.id` and returns `item`, while
still forcing Actions disabled. Supplying the existing ID updates that draft.

Allocation and publication use the same registry writer; collisions retry without
overwriting another item. IDs retain the lowercase/number/underscore format and
32-character limit. Creation without an ID is not idempotent: after an unknown
response, list and reconcile before retrying rather than creating another item.

The core **Actions** view (`/react/actions`) stores reusable operation definitions in
`data/actions/registry.json`. Groups contain Action IDs, so an Action can be shared
without duplicating its source. Nothing is registered until the user builds a Group.

An Action has an ID, name, description, enabled flag, effect, input JSON Schema,
execution definition and output format. The Panel supplies typed scalar input fields;
the draft API accepts an object JSON Schema with `additionalProperties: false`.
Schema references are rejected. Draft edits do not modify built releases.

## Execution scope

- Internal API Actions fix `method: GET` and one of `/api/sessions`, `/api/models`,
  `/api/workspaces`. Inputs become query parameters. The provider connects only to
  the owning Pan instance's explicit loopback HTTP endpoint, without redirects or
  proxy environment routing. Arbitrary URLs, headers, route templates and write APIs
  are unavailable in this first implementation.
- Commands require an absolute existing executable and working directory, a string
  argv array and a timeout of 1–30 seconds. A placeholder such as `{value}` must occupy
  one entire argument and reference a required scalar input (or an input with a valid
  default). Pan uses subprocess exec, never shell interpolation. Shell/script launchers
  are rejected. The user must review the fixed executable and arguments at build time;
  a program can itself interpret its arguments or access local resources. This is not
  an OS sandbox or executable-content snapshot.
- Effects are `read`, `write_local`, `write_external`, `high_risk`. API Actions are
  read-only. High-risk build/execution is refused because no high-risk approval policy
  exists here. Build is the user's approval of the other exact definitions; Group
  membership does not grant extra rights. JSON output must parse; text output preserves
  stdout. Nonzero exit, timeout, malformed output and unconfirmed execution are errors;
  callers must not automatically retry side effects. Each command stream and decoded
  API response is limited to 512 KiB; the complete JSON-encoded tool result is limited
  to 1 MiB so escaping and duplicated structured content cannot overflow the generic
  provider reader. API reads also have a 30-second overall deadline.
  On Windows a host-owned helper enters the existing kill-on-close Job Object before
  launching any command, so timeout/exit also retires descendants. Other platforms
  use an owned process group for timeout cleanup. No new service or Worker lifecycle
  is introduced. Confirmed tool errors remain per-call results and do not poison the
  generic provider connection for later explicit calls.

## Build, versions and Session bindings

Build validates every selected Action, rejects disabled/empty selections and writes
an immutable snapshot and capability file under `data/actions/builds/<group>/<version>/`.
The atomic registry pointer publishes an ordinary `pan_api` plugin named
`action-group-<group-id>` with the Group's display name. Provider descriptors are
derived from the registered snapshot and never added to the editable manifest.

Use the existing Session plugin UI or the Actions Panel's shared selection component:

| Mode | Access |
| --- | --- |
| `standalone` | Group MCP shim, tools `action-group-tools.echo` |
| `pan_native` | Generic Pan projection, tools `pan_action_group_tools_echo` |
| `gateway` | Generic `pan-tools` with `api_discover` and `api_call` |

Only enabled bindings in the Session's selected mode are visible. Core Pan tools do
not become enabled merely by selecting native mode. Worker startup pins Group plugin
and provider definitions; source edits and rebuilds cannot change that loaded version.
Rebuild requests the normal idle/deferred Worker restart for enabled live bindings,
including gateway Groups. Other gateway plugins retain their existing dynamic policy.
Enabling a new Group on a live Worker also requests that restart, even if an existing
gateway leaves the physical descriptor unchanged. The runtime withholds that Group
until Worker startup captures its release.
Old release files remain available until unbuild. Version pinning freezes definitions,
not executable bytes, external files or API data.

Unbuild checks **live Worker** status. Only an enabled Group in a `running` Worker
blocks it; the HTTP 409 lists every blocking Session name and ID. Idle Workers do not
block. The operation removes logical bindings and generated connection descriptors,
unregisters the Group atomically, removes its release files and requests the standard
idle restart. Registry tombstones also invalidate stale saved descriptors at compile
time. Session persistence failure before unregister restores previous configurations.
Every affected Session is restored in memory before rollback writes begin; all restore
writes are attempted. A failed rollback write is explicitly reported with its Session
ID and requires storage recovery; the API does not claim durable rollback succeeded.
Admission uses loop-local Group/Session reservations. Registration and release of
reservations are short in-memory operations; no global mutex is held across IO,
Worker startup or Session persistence. Only overlapping resources wait. Unbuild
exclusively reserves the Group before collecting and reserving referencing Sessions;
the real queue/native handoff boundary cannot admit old loaded tools after detach.
Storage operations run off the event loop and preserve ordered Session persistence.
Cancellation of a multi-file release waits for its transaction/rollback to finish
before admission is released. Reservations are process-local, not a distributed lock
or a crash-recovery journal.

## Agent drafts and HTTP

`action_drafts(operation="list"|"put"|"delete", kind="actions"|"groups", item=..., item_id=...,
expected_revision=...)` is a core Pan MCP tool. Agent puts always force Actions back
to disabled drafts. This tool cannot build, enable, unbuild or execute Actions.
Built Group tools reuse the existing plugin runtime and capability protocol.
Pan's current local API trust boundary is unchanged; this is a tool-surface restriction,
not a new HTTP authentication or OS-user separation scheme.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/actions` | Sources, registry revision, release status, blockers and bindings |
| `POST /api/actions/{actions|groups}` | Create with optional ID, body `{item, expectedRevision?}`; response includes created `item` |
| `PUT /api/actions/{actions|groups}/{id}` | User save, body `{item, expectedRevision?}` |
| `DELETE /api/actions/{actions|groups}/{id}?expectedRevision=N` | Delete unreferenced source |
| `POST /api/actions/groups/{id}/{build|unbuild}` | User release operation, `{expectedRevision?}` |
| `POST /api/action-drafts/{actions|groups}/{put|delete}` | Agent draft operation |

Mutations accept the registry revision to reject stale editors. A referenced Action
cannot be deleted. A built Group must be unbuilt before deletion. An edited Group's
draft Action selection can differ from its displayed **Exposed Actions** release.

## Reproduce focused validation

Run Python commands through `scripts/run_tests_isolated.py`; pytest's shared storage
fixture also isolates the Action store via the Session data parent. No Action store
environment override or import-time write is introduced.

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_action_groups.py tests/test_plugin_session_config.py tests/test_plugin_platform.py tests/test_test_isolation.py -o addopts= -q
cd packages/web
pnpm exec vitest run src/views/ActionsView.test.tsx src/components/session/PluginSelection.test.tsx
pnpm build
cd ../..
python scripts/run_tests_isolated.py -- python scripts/verify_action_groups.py
```

The real probe starts only an owned HTTP server and generic plugin runtime on an
OS-assigned port, stores all state under `.validation/action-groups/`, launches the
production browser, and calls both an API Action and command Action over actual stdio
MCP in all three modes. It starts no model, gateway or deployed service. Logs, JUnit,
screenshots and `latest-live.json` are local evidence, separate from unit results.
