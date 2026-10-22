# Trusted new-Worker provider launch binding (credentialless first path)

This revision implements the launch pipeline, not production account recovery.
Default disabled: `PAN_CODEX_FIXED_PROVIDER_LAUNCH=1` AND the existing user-enabled
quota host AND a trusted in-code CodexAdapter.provider_launch_source are required.
No source is installed by production in this batch: current cc-switch CLI has
no immutable provider configuration export. No public API/Session field can set
that source or supply a verified launch/receipt. Existing default startup remains
unbound. No account/native/managed ID equality is required or inferred.

## Supported fixed process configuration

Audit of CodexAdapter.build_spawn_args and app_server_wrapper._server_options
shows existing `-c key=value` overrides are forwarded to the native app-server.
Offline installed Codex CLI `app-server generate-json-schema` (owned empty home,
exit0, no account/provider calls) confirms thread/start and thread/resume accept
modelProvider and their responses return modelProvider; config/read allows
additional config properties. Schema evidence is in artifacts/provider-launch.
Schema generation alone does not establish a real account/config read result.

The trusted source returns exactly `{providerId,exportReceiptId,config:TOML}`.
It must be a single immutable owner export, not a global-ID read followed by an
unrelated config-file read. Current implementation accepts only one provider,
base_url/wire_api=responses/requires_openai_auth=false, optional name, no
credentials/env_key/headers/secret fallback, includes, scripts or extra fields.
Config <=64KiB. Disabled provider IDs are rejected. Native-control/oneshot and
non-Windows paths remain unbound; no global profile is read to invent an export.
After an explicit supported export is attempted, failure stops that spawn with
a sanitized error; it never silently runs another provider as the requested one.

The owner snapshots this provider-only definition under its existing data root,
automation/private-launches/<random launchId>. Existing Windows owner-only DACL
creation and readback are reused for directory/config/manifest. Reparse paths
are rejected. No secret config is copied in this supported mode. A private
per-launch capability authenticates the parent/wrapper handshake; it is never
written to the Session ledger. This is same-user trust, not isolation from a
malicious process running as the same OS user.

The trusted wrapper derives explicit process `-c` overrides using a unique
pan_fixed_<launchId> provider key. They take precedence over mutable global
provider definitions and prevent table merging with an old credentialed key.
The original CODEX_HOME/native profile/history are retained, never replaced by
the temporary snapshot. No global config write, history copy or auth.json copy.
The snapshot is checked again before attestation. A live global provider/config
change cannot substitute its identity into this already pinned process.

## Actual receipt chain

1. Parent prepares snapshot off-loop, launches the existing wrapper and assigns
   its retained process to existing JobObjectGuard. Wrapper authenticates its
   actual PID/raw FILETIME before any native child can start. This handles
   Windows venv launchers whose interpreter is a different descendant PID.
2. Parent verifies ancestry and FILETIME and enrolls that interpreter in the same
   Job, then sends the one-use start capability. Native descendants inherit Job
   ownership, including through another venv launcher. No offline Session spawn
   or second Worker/controller is introduced.
3. Wrapper initializes its actual native child, reports retained native PID/birth,
   performs config/read, and requires the selected provider definition to match
   the pinned projection. Missing/unsupported/redacted provider fields are not
   guessed. Native identity must remain the same and belong to the launch Job.
4. Thread start/resume explicitly requests modelProvider=the fixed key. Wrapper
   requires the returned modelProvider to agree before emitting a signed final
   receipt. Process defaults alone cannot prove what a resumed thread uses.
5. Parent validates capability, projection digest, native identity and thread
   receipt. Existing ordered Session writer commits providerLaunchBinding on a
   completed/safeTerminal provider-launch execution operation. Only after the
   write and in-owner epoch/Worker/generation/token checks is an in-memory usable
   binding published. Write failure never publishes it. Earlier/new generations
   cannot borrow the receipt, even if a disk write finishes late.

Persisted fields: version/domain/providerId, launchId/exportReceiptId, safe config
and projection digests, runtimeEpoch, sessionId, workerId/generation, retained
owner/wrapper/native PID birth identities, nativeThreadId, verifiedAt. No config
body, URL, capability, auth, executable snapshot or private manifest path. The
launch operation has executionSeq0 and is completed/safeTerminal; it creates no
unfinished business task. Existing operation compaction/lifecycle remain owners.

Host Bindings consumes only a binding whose live Pending owner is the exact
Worker and whose operation, token, epoch, generation, retained owner process,
actual interpreter and native process still match. Reloaded audit records or
branch-copied fields cannot create a live Pending owner. Historical Workers are
never backfilled from current provider/profile. Native quota profile metadata
retains its previous independent contract; it is not a provider ID mapping.

## Lifecycle / task separation / cleanup

All current stream create/restart/respawn/branch paths use _spawn_process and
_claim_offline_owner. Each new process obtains its own immutable export and
receipt; branch does not inherit a parent binding. Native SessionsProvider fork
continues to own native history materialization before that new spawn. Existing
Group pin, Pause/Illegal/lease and queue lifecycle are unchanged. A branch owner
save failure now kills/unregisters the owned new process, matching normal create.

_restart_tasks still clears current task identity. Native thread resume and a
valid launch binding do not restore an old unfinished execution operation. Any
future explicit legal continuation must admit a new task execution/sequence and
record its predecessor separately after the existing gates. Without that fresh
evidence host task remains None and idle recovery/wake stays blocked. This batch
does not implement that continuation or account/model switch effects.

An owned process exit triggers cleanup: terminate/query its existing Job to
zero members, close retained handles, delete only its exact private directory.
Cancellation retires in-flight snapshot/Job setup before cleanup. Unconfirmed
cleanup raises and retains ownership/files rather than reporting success.
If the whole Pan host crashes, the Job kill-on-close fences descendants, but
private directories can remain. No automatic crash-directory deletion is added:
startup lacks retained owner handles to prove those historical files inactive.
They are owner-only and contain no provider credentials in this mode; they do
not authorize replay/binding. Explicit orphan inspection/cleanup is a remaining
boundary, not a claimed restart cleanup capability.

## Actual upstream work still required

Production Bridge list/quota/switch is unchanged; no guessed export command is
called. Small upstream extension needed: its existing trusted owner must select
providerId and materialize a versioned provider-qualified immutable launch export
in one operation, with non-secret exportReceiptId/config digest and private
credential transport/reference if needed. Never return credentials in normal CLI
JSON or accept client verified=true. That real exporter can then be installed as
the trusted source after review. Official credentialed account launches remain
unsupported until credential pinning/rotation/cleanup and native attestation are
proved. Keyring/OAuth fallback is not inferred from provider or native IDs.

If a native version does not expose the provider definition in config/read, it
needs a non-secret resolved-provider digest/receipt or equivalent owner proof;
this implementation returns unconfirmed, not a fabricated binding. Native control
OwnedKernel requires a retained-process/receipt integration before opt-in there.

Validation uses real Pan wrapper/lifecycle, real owned native-protocol fixture
process and loopback fake provider, plus owned HTTP spawn/restart. It proves
controlled child configuration use (actual loopback request), not a real official
account or installed Codex native execution. Installed CLI schema generation is
separate evidence. No paid turn or real account/provider query was performed.
