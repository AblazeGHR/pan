# Private provider exporter consumer candidate

- Original isolated tree / branch: `D:/project/pan-worktrees/event-trigger-core-20261022`,
  `feature/event-trigger-core-20261022`.
- Accepted baseline: `5f44bbf5e2da8a3af1f20c65d2ac53d6c3fcc236`.
- Exact final tested source: `ac21e53b30a60b25865a5b8892fecc9bfd3bf93d`.
  Later evidence-only commit adds this report/JUnit/logs, not product source.
- Status: TA candidate; await MA review. No deployment flags were applied.

## Result and honest production boundary

Pan now consumes a versioned private export on its actual existing spawn path,
behind all existing gates plus default-off `PAN_CODEX_CC_SWITCH_PRIVATE_EXPORT`.
It does not read global config/auth files to invent a provider identity and does
not require managed/native/provider IDs to match. The protocol is internal to the
trusted host/Bridge, not a public MCP/Panel/Session verified flag or parallel registry.

Current cc-switch source cannot supply atomic export. Read-only audit at
`D:/project/cc-switch-pan` SHA `324014cd149c4a12e73b689fde092505c83d89e1` found no
private launch command/capability or exposed monotonic revision. baseline-gap.json
records both that gap and the old Pan requirement for an injected source. This
is source evidence, not a real account/CLI query or a fabricated failing model run.
No outside-repository source was modified. The exact upstream function/file
locations, closed protocol and six-part minimum patch design are in
`docs/design/provider-private-export-v0.3.md`.

### Implemented and actually consumed

1. Bridge probes only --version first. Without exact capability
   `privateLaunchExport:pan-cc-switch-launch/v1` it does not request any export,
   inspect auth data or publish a binding. Default absent/off performs no probe.
   Advertised failures stop the requested guarded spawn with sanitized errors.
2. Pan creates its owner-only per-launch request directory and one-time private
   request/MAC key. Upstream returns a bounded safe receipt and writes only the
   private bundle in that exact directory. Strict closed fields, provider domain,
   positive revisions, nonce/epoch, expiration, HMAC, ACL, size, reparse/hardlink,
   duplicate JSON, provider-only TOML and credential-mode checks are enforced.
3. Supported credential modes are none or fixed provider API-key bearer-env.
   Token is a randomly named owned child environment entry, never argv, config
   override value, Session ledger, event or log. Wrapper checks exact handoff and
   native config/read env-key route; private bytes/digest stay in private files.
   Exact token redaction occurs before output buffers/forwarding. Malformed
   bearer strings/unknown modes/ambient auth routes fail closed.
4. Reuses Pending, wrapper/native thread receipts, Job ownership, existing launch
   operation persistence and host inventory. The safe optional `privateExport`
   audit records only owner/provider/selection/credential revisions and validity
   timestamps. No secret/bundle digest/MAC/private capability is persisted there.
5. The private environment/ACL/file read is moved off the event loop via existing
   lifecycle.io; no long Session/policy/Bridge lock is held across export I/O.
   Cancellation retires the off-loop export before existing exact-directory
   cleanup. Write/native-receipt failures publish no usable binding.

No existing policy effects, Pause/Illegal/lease/Group behavior or public APIs were
expanded. Existing injected credentialless source grammar stays strict. Migration
is additive optional safe audit on new launch records, with flags absent/off and
old CLIs unbound. An incompatible advertised exporter stops that spawn rather than
silently selecting an unpinned provider; do not activate flags before upstream
owner implementation and independent acceptance.

## Executable final validation

In the core tree, with the tested source frozen:

```powershell
$env:TEST_REAL_CODEX_JS='D:/node_npm/node_global/node_modules/@openai/codex/bin/codex.js'
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/verify_provider_export.py
```

verification.json retains full per-check argv, exact SHA, exits, durations and
encoding. Every pytest group uses scripts/run_tests_isolated.py, own data/native
home and synthetic producer/provider. Final Windows environment: utf8Mode=0,
locale cp936, no PYTHONUTF8/PYTHONIOENCODING overrides. All new text IO/decoding is
explicit UTF-8. No dependency installation/shared dependency mutation/full suite.

| Check | Tests | Failed/errors/skipped | Exit |
| --- | ---: | --- | ---: |
| Private export protocol/lifecycle/adversarial/concurrency | 40 | 0/0/0 | 0 |
| Installed Codex + actual wrapper + synthetic bearer loopback | 1 | 0/0/0 | 0 |
| Prior launch/native compatibility regressions | 44 | 0/0/0 | 0 |
| Existing quota provider source + CLI/error normalization compatibility | 41 | 0/0/0 | 0 |
| Diff check from accepted baseline | — | — | 0 |
| Targeted compile | — | — | 0 |

126 tests passed. JUnit/log names match the check names in this directory.
Tests include deterministic export-vs-switch barriers, A->B->A monotonic revision,
in-place provider/credential edits, switch after coherent capture without
relabeling, host disable mid-export, malformed/unknown/auth mode, MAC/material
changes, file size/hardlink and injected insecure-ACL refusal, expired receipt,
private environment off-loop/readback, cancel, cleanup, native mismatch and
Session binding-save failure. There is no guessed success/automatic replay.

### Real native evidence and fixture boundary

Installed CLI is `codex-cli 0.163.0` via real Pan wrapper and installed Node/Rust
app-server. The upstream exporter is an **injected synthetic protocol owner**;
this proves the Pan consumer, not a cc-switch implementation that does not exist.
Only local HTTP Responses service and generated synthetic bearer are used.

real-cli-evidence.json records:

- Two actual successful local `/provider-a/responses` requests, model owned-model,
  with only boolean `syntheticAuthorizationMatched:true` (headers/token omitted).
- Start and next-process resume of the exact same real native thread, each with
  a new pinned alias/receipt and durable operation; safe privateExport revisions.
- Synthetic global owner switched to provider-b after atomic export, before
  native startup; the child remains bound to exported provider-a. During an
  actual blocked local request the owned global config changes, with no rebinding.
- CODEX_HOME/history retained by product code; test sets a new empty file-auth
  home and checks its sentinel. No real credentials, history or user config read.
- Existing Job-member images include installed codex.exe. Retained member handles
  confirm termination for both starts, ownedProcessesRemaining=[], private launch
  directories removed, providerPortReleased=true, ownedProfileRemoved=true.
- Exact generated token is checked absent from safe evidence and persisted Session
  JSON; no secret values/digests are in this report or Git evidence.

## Remaining blockers and next authorized handoff

| Capability | State / next step |
| --- | --- |
| Pan private exporter consumer | Implemented, default off, tested with synthetic owner and actual CLI. |
| Current cc-switch atomic owner export | Missing. Apply the documented minimum upstream patch in a separately authorized task; this tree does not modify that project. |
| Atomic revisions and cancel/IPC-loss fencing | Required inside upstream owner/DAO/credential manager. HMAC is same-user trusted-owner binding, not proof that arbitrary exporter code obeys atomicity. |
| Credentialed official ChatGPT OAuth | Blocked/rejected. OAuth access token is not an API-key export; requires distinct native per-process auth/immutable reference or revision-pinned owner proxy with account/header/refresh proof, retaining CODEX_HOME. |
| Credentialed official API-key route | Pan supports only an explicitly exported fixed bearer-env Responses route; no real official-provider test or production producer is claimed. |
| Real deployment or account/Worker effects | Not enabled/executed. Existing quota mutation ports, explicit task continuation and native-control boundaries remain. |
| Whole-host crash secret cleanup | Still owner-only orphan inspection boundary; no automatic recursive crash cleanup/replay introduced. |
| Full production HTTP/UI/operator acceptance | Not run in this batch. Actual local provider HTTP + wrapper/CLI is covered; MA/human acceptance remains separate. |

Upstream must reuse the existing AppState/IPC, app switch coordinator and provider
DAO/credential lifecycle revision guards, capture briefly, build/write off-lock,
recheck revisions before completion, and never read a subsequently changed global
file to label a Worker. Source-side cancellation must retire all writers and never
recreate a removed request directory; Pan's off-loop cancellation retirement does
not repair a nonconforming detached upstream writer. Advertise the new capability
only after those owner-side deterministic tests pass. Do not deploy flags based
on this synthetic owner proof alone.

No main/practical/integration movement, push/release, real account/profile/provider
query, user Worker operation, protected port 8767/8768 access or paid model call.
Stop at this candidate and await MA review.
