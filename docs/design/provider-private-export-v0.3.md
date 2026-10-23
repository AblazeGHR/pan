# Private provider launch export v1 (Pan consumer; upstream capability pending)

This document defines the narrow consumed protocol and exact minimum upstream
work. It does **not** claim that current cc-switch implements atomic export.

## Source audit, no real account access

Read-only source: `D:/project/cc-switch-pan`, commit
`324014cd149c4a12e73b689fde092505c83d89e1`.

- `src-tauri/src/pan_minimal_cli.rs`: `execute`, command-shape whitelist (~56),
  provider list (~135), switch (~149), quota (~155). `--version` advertises only
  `pan-cc-switch-gui/v1`. No launch export, provider revision or credential receipt.
- `src-tauri/src/pan_cli_ipc.rs`: `forward` and GUI execute routing share GUI
  AppState, returning bounded JSON. The CLI lifetime owner lock is not a provider
  configuration/credential revision lock; it cannot prove a coherent export.
- `src-tauri/src/services/provider/mod.rs`: `ProviderService::switch_inner`
  (~6578) uses `mode::controller::lock_settled_blocking` (~6609), also used by
  provider add/update paths (~5561/6139). These are reusable coordination seams.
- `src-tauri/src/mode/controller.rs`: `lock_settled` (~676) acquires the app switch
  mutex and settles pending mode operations. No export contract is present.
- `src-tauri/src/database/dao/providers.rs`: `save_provider` (~266),
  `set_current_provider` (~475), `update_provider_settings_config` (~497).
  There is no exported monotonic provider/selection version in the current CLI.
- `src-tauri/src/services/provider/codex_direct.rs`: `managed_account` (~68),
  prepare (~174), official-row preparation (~214), fixed projection/route/auth
  building. These manipulate native live state; reading those written global
  files later is not an atomic provider-qualified launch export.
- `src-tauri/src/proxy/providers/codex_oauth_auth.rs`: CodexOAuthManager (~372)
  owns accounts/default/access-token maps, lifecycle/storage/refresh locks.
  Managed default, refresh, revocation and provider edits span separate state.
  Their current API does not promise one immutable provider+credential revision.

No executable cc-switch/account query, global config snapshot, credential-store
read or source modification outside this Pan tree was performed. Provider IDs
remain in `cc-switch-provider:codex`, independent of native/managed IDs.

## Activation and consumed path

All gates are required: existing `PAN_CODEX_FIXED_PROVIDER_LAUNCH=1`, user-enabled
quota host, Windows stream wrapper path, plus new host-only
`PAN_CODEX_CC_SWITCH_PRIVATE_EXPORT=1`. Both flags default absent/off. Native
control remains unbound. Existing trusted test/source injection keeps its old
credentialless grammar; this is not a new Session/API/client verified flag.

If no in-code source is installed, the existing prepare function uses
`packages.cc_switch_bridge.launch_export.prepare`, probes the existing Bridge
executable verification path and calls `--version`. Only exact
`privateLaunchExport: "pan-cc-switch-launch/v1"` opts into the internal command.
Old CLIs cause no export/config/account command and return no Pending/binding;
ordinary spawning remains unbound/unknown. Advertised export failure stops that
spawn with the existing sanitized error, not an unpinned fallback. No lock is
held across CLI I/O; the existing plugin Panel lock and event loop are not used.
There is no poller, account registry or public plugin capability for this command.

### Request and private output

Pan allocates an unpredictable existing owner-only directory beneath its own
`automation/private-launches/<launchId>`, verifies non-reparse ancestors, and
creates owner-only `request.json` exclusively. Command:

```
cc-switch --app codex provider launch-export <absolute-request.json> --json
```

Request exact shape:

```
{contract:"pan-cc-switch-launch/v1", requestId:<32 lowercase hex>,
 receiptKey:<32 random bytes as lowercase hex>, app:"codex",
 selection:{kind:"current"}, createdAt:<Unix milliseconds>}
```

Selection is captured **inside the owner atomic operation**. Pan never queries a
current ID and later guesses a matching config file. The current v1 request has
no caller-supplied provider, managed account, credential path or arbitrary fields.

The owner writes exactly `bundle.json` in that existing request directory using
CREATE_NEW, owner-only DACL, no reparse/hardlink following or directory creation,
flushes/closes it before return. It must not copy auth/profile/history or output
credentials to stdout/stderr/pipe JSON. The CLI/GUI operation must be synchronous:
no detached exporter, no background writes after return/cancel/deadline, no
recreation of a removed directory. A forward timeout must cancel/retire the GUI
request or close the already-open output handle; returning a receipt after an
uncertain operation is forbidden. Pan accepts <=128KiB, regular single-link
owner-only files, no reparse entries, strict UTF-8 JSON with duplicate keys denied.

Bundle exact shape:

```
{contract:"pan-cc-switch-launch/v1", requestId:<same>,
 config:<provider-only TOML, <=64KiB>,
 credential:{kind:"none"} | {kind:"bearer-env",token:<private ASCII bearer>}}
```

Config uses the existing strict single provider `model_provider/model_providers`
grammar: name optional, base_url, wire_api=responses, requires_openai_auth=false;
no env/auth/headers/includes/scripts/extra keys. Bearer mode requires HTTPS or
literal loopback HTTP; credentials in URLs are rejected. The credential is
16..16384 ASCII bytes in bearer-token grammar [A-Za-z0-9._~+/\-]+=*;
quotes, backslashes and whitespace are rejected. The raw bundle never enters
Git, logs, ledger, events or report. Tests generate random synthetic tokens only.

### Public receipt (stdout)

Exact keys:

```
{contract, requestId, receiptId:<random32hex>, ownerEpoch:<random32hex>,
 providerId:<provider-domain opaque ID>, providerRevision:<positive int63>,
 selectionRevision:<positive int63>,
 credentialSource:{kind:"none"|"provider-api-key",revision:<positive int63>},
 issuedAt:<Unix milliseconds>, expiresAt:<Unix milliseconds>, bundleMac:<hex HMAC>}
```

Receipt has no config, URL, token, private path, managed/native ID, client
verified flag or returned arbitrary metadata. Source kind must agree with bundle
credential kind. issuedAt is >= request.createdAt and <= local now+2s; expiresAt
is future and <=issuedAt+30s. Receipt is <=16KiB. Positive monotonic revisions and
opaque owner epoch bind a particular owner snapshot; they must not be timestamps
or content hashes of credentials. Expiration is rechecked before native spawn
handshake and before accepting its final attestation.

`bundleMac = HMAC-SHA256(receiptKey, canonical(unsignedReceipt) + LF + rawBundle)`.
Canonical is UTF-8 JSON, ASCII field names, recursively sorted keys, compact
separators, ordinary JSON integer/string encoding. ReceiptKey remains private
in request.json and is never placed on argv/stdout/ledger. This protects binding
and accidental cross-request swaps, **not** a malicious same-OS-user process.
The trusted configured executable/GUI owner remains the authority.

Pan verifies this receipt before creating the existing Pending. It renames the
provider to the existing unpredictable process alias and adds one random
`PAN_CODEX_LAUNCH_TOKEN_<launchId>` env-key route. Token goes only in the owned
child environment (not argv); wrapper checks the private bundle digest and the
exact env handoff, config/read must attest that exact env-key plus fixed endpoint.
No other env/header/auth route is accepted. Wrapper redacts this exact secret
from diagnostics/events before buffering or forwarding. Native requests and
thread/start/resume still require the normal actual CLI receipts. Same CODEX_HOME
and native history are retained; no auth.json/keyring/global file is written.

Only safe `privateExport` audit is added to the existing launch operation:
contract, ownerEpoch, provider/selection revisions, credentialSource kind/revision,
issuedAt/expiresAt. No credential/bundle digest, MAC/key, environment value, config
body or executable credential snapshot is persisted. Existing config/projection
digests contain only non-secret fixed routing. Publication still follows ordered
Session persistence and Worker/epoch/generation/token checks. Cancellation waits
for the off-loop export to retire, then deletes only its own private directory.
Normal exit/failed attestation/write failure reuse existing Job/handle cleanup.
Crash orphan cleanup remains explicitly unimplemented, as in the earlier batch.

## Exact minimum cc-switch patch (not applied outside this repo)

1. In `pan_minimal_cli.rs::execute`, add the one exact command shape and dispatch
   to a new `services/provider/launch_export.rs::export_codex_private(state, path)`.
   Add `privateLaunchExport` to --version **only after** all guarantees/tests below
   pass. Route through existing `pan_cli_ipc` to the single GUI AppState owner;
   keep unsupported/current legacy GUI conflicts fail-closed.
2. Add durable positive provider revision, Codex selection revision and
   credential-source revision to existing DAO/migration, increment in the same
   transactions as `save_provider`, `update_provider_settings_config`, delete,
   `set_current_provider`. Include A->B->A changes, in-place edits, managed default
   and refresh/revoke changes that affect a resolved source. Never reset/reuse a
   revision on row replacement. ownerEpoch is new random value for GUI owner
   lifetime; only safe revisions are exposed, not credential hashes.
3. Reuse `mode::controller::lock_settled(state,Codex)` for a short capture: read
   selected provider row/revision, exact credential-source binding/revision and
   already-resolved immutable credential under established manager lock order.
   Forbid a fallback to current live auth/config or implicit mutable managed
   default. Do **not** await refresh/network/file writes under the global gate.
   If credentials require refresh, release, refresh via existing manager, then
   retry capture with new revisions or return conflict (bounded single request).
4. Build provider-only TOML and private bytes from that capture off-lock. Reenter
   the same coordinator and credential lifecycle read guard, recheck owner epoch,
   selected ID plus monotonic selection/provider/source revisions. Any change,
   including ABA, causes conflict and no success receipt. The coherent capture
   is the export linearization point; a subsequent switch does not relabel or
   invalidate the immutable already-exported new process identity. No history
   worker is backfilled from that selection.
5. Validate request ACL/owner/reparse/path/age before capture; use a retained
   directory handle and exclusive owner-only relative bundle handle. Keep that
   retained handle for cancellation/IPC-loss fencing; never reopen/recreate a
   disappeared path. Flush/close before safe MAC receipt return. Bound whole
   command under Pan's 8s deadline; never log bundle/token/request key. Add
   positive and malformed/ACL/revision/ABA/cancel/timeout tests in this owner.
6. First advertise only supported `none`/fixed provider-api-key captures.
   **Official ChatGPT managed OAuth is not equivalent to bearer-env API-key.**
   It also needs account headers, refresh semantics and native auth-mode proof.
   Add a distinct future protocol version/mode only after Codex supplies a
   per-process immutable credential reference or an owner proxy pinned to that
   provider/account revision. That proof must retain user's CODEX_HOME and avoid
   ambient keyring/auth fallback. Current Pan rejects `managed-oauth`/unknown
   modes; do not turn an OAuth access token into an invented API-key export.

## Verified versus still blocked

Pan's default-off production consumer is now wired and locally verifiable with
an injected protocol-compliant synthetic owner. Actual installed Codex 0.163.0
successfully receives a random synthetic bearer at the owned loopback endpoint,
starts and resumes the same thread with new pinned aliases and durable bindings.
Tests cover owner switch/export conflict, ABA/in-place/credential revision edits,
switch after capture without relabeling, host disable during export, malformed
receipt/MAC/material, expiry, native/persistence failure, cancel/cleanup, and
secret redaction. No real cc-switch export implementation is claimed.

Blocked: current installed/source cc-switch cannot advertise atomic export;
credentialed official OAuth mode and production policy effects are not wired;
no real credential/profile/provider/Worker operation, UI, push or release was
performed. Upstream owner implementation and independent security/acceptance
review are required before changing deployment flags. No new account registry,
policy replay or automatic switch/restart/wake was introduced.
