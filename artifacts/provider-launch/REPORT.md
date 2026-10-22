# Trusted new-Worker launch candidate (conditional credentialless path)

Base ff7c5d7830b9b8ea128e0f99674eec9f0015118a, original core worktree and
feature/event-trigger-core-20261022. Exact tested executable source:
084b3ab20fbd6f4ba5a63464446f3e5d6f499e8e. Following evidence commit does not
change executable source. This candidate is not MA/human acceptance and is not
production official-account recovery.

## What actually runs

Real Pan _spawn_process -> existing Codex wrapper -> real owned native-protocol
fixture process -> owned loopback fake provider -> config/read + thread response
attestation -> existing ordered Session operation write -> host inventory binding.
Tests observe the child HTTP request to the fixed endpoint, not just equal global
provider values. Actual create, restart and branch lifecycle entry points run;
native SessionsProvider fork/history methods are injected for that branch test.
Real owned HTTP spawn/restart/Kill runs through production endpoints. No real
Codex account, native history database or model turn is used for this path.

Installed Codex CLI was separately invoked only for offline app-server JSON
schema generation with an owned empty CODEX_HOME: exit0. ThreadStart/Resume
params and responses support modelProvider. Config/read allows additional fields;
it does NOT promise every native version exposes resolved provider definitions.
The full generated schema/command evidence is codex-schema-probe.json. This is
not an installed-native config/account execution attestation.

## Requirement / evidence / remaining matrix

| Requirement | Implemented / evidence | Remaining boundary |
|---|---|---|
| Actual immutable per-process config | Trusted owner source snapshots <=64KiB provider-only TOML; wrapper derives unique per-launch provider key and highest-priority native -c overrides; selected config is read back from child. Global config/source changes after export still hit only the frozen endpoint. | Production Bridge has no immutable export capability, so no production source is installed. No guessed command or global profile fallback. |
| Preserve native home/history | Original CODEX_HOME retained, provider-only overrides; own native-history sentinel and global config remain unchanged after launch/cleanup. | No real native history/resume/fork DB acceptance; those existing implementations are unchanged. |
| Process and thread confirmation | Authenticated actual-interpreter bootstrap handles Windows venv launcher PID difference; existing JobObjectGuard enrollment precedes native spawn. Retained owner/interpreter/native FILETIME, config projection and thread modelProvider confirmed. | Native versions missing provider config/readback or thread confirmation fail unconfirmed. Native-control OwnedKernel/oneshot remain unbound. |
| Durable binding | Completed safe provider-launch operation, executionSeq0, providerLaunchBinding contains domain/ID, source receipt ID, digests, runtime/Session/Worker/generation/process/thread identities. Published only after ordered save and identity recheck. | Runtime binding is not recovered from historical disk records. No executable config/credential/capability in ordinary Session ledger. |
| Strict host consumer | Binding requires matching live Pending owner, durable operation/token, runtime epoch/generation and all retained process identities. Old profile-map fallback removed from production. Even supplied old mapping cannot backfill a Worker. | Old Workers and unsupported new launch paths remain accountId unknown. |
| Shared lifecycle | Create/restart/respawn/branch use existing spawn + owner claim hooks; fresh launch receipt each time. Branch owner save failure kills/unregisters the new process. Kill/restart await launch Job/handle/private-file cleanup. | No second Worker/Job controller. Group release/pin and business gates untouched. |
| No task resurrection | _restart_tasks unchanged: clears current task. Launch operation is already completed/safe and has no unfinished task identity. New native thread/process receipt alone never grants wake eligibility. | Legal continuation needs a separately admitted fresh execution and predecessor audit; not implemented and no wake effect connected. |
| Persistence / concurrency | Actual _save_body failure does not publish in-memory or disk receipt. Generation changes before/during ordered writes cannot publish usable binding. Source global-change barrier and unrelated Session queue progress tested. | Atomicity is existing per-Session writer, not claimed across files or external GUI. |
| Private data | Reuse Windows owner-only directory/file creation + ACL readback. Reparse paths rejected; first mode rejects auth/env_key/headers, credentials and public verified flags. Capability only in private manifest; normal ledger has safe digests/references. | Credentialed official exporters/keyring/OAuth pinning deliberately unsupported. No actual credentials read/copied/written. Same-OS-user malicious code is outside ACL boundary. |
| Cleanup / cancellation | Source preparation and Job setup retire before cancellation cleanup; boot/receipt failure stops owned process/Job and removes private files; native exit watcher closes Job/handles, verifies zero members, deletes exact owned directory; lifecycle retirement awaits watcher. HTTP kill leaves no private config. | Full-host crash may leave an owner-only noncredential pending directory; no automatic deletion without owner proof. It is not a usable binding and cannot replay. Explicit orphan inspection/cleanup remains unimplemented, as documented. |
| Default off | PAN_CODEX_FIXED_PROVIDER_LAUNCH must be exactly1, quota host enabled, trusted source callable, supported mode. No public API can install source/verified receipt. Production source absent. | Existing host read opt-in alone does not turn on this launch mode or any effect. No real profile flag applied. |

## Concrete upstream work, not empty effect ports

The existing cc-switch owner needs one atomic provider-qualified launch export:
versioned providerId + immutable definition + safe exportReceiptId/digest, and a
private credential transport/reference for credentialed providers. Current list/
quota/switch output cannot supply that proof. Export must be produced by that
owner, never global-ID + unrelated-file sampling or client verified=true. No such
command is invoked in this batch. Installing that reviewed exporter as the trusted
source is the remaining host integration.

For native versions without resolved provider data in config/read, a non-secret
resolved-provider digest/attestation is needed upstream. Credentialed official
account startup, native-control integration and explicit new task continuation
still need their own verified handling. Production account switch/restart/wake
ports remain unbound; this batch neither adds nor executes policy effects.

## Frozen validation

Command: D:/project/Pan-main/.venv/Scripts/python.exe scripts/verify_provider_launch.py.
verification.json records source SHA, exact argv, exit codes, interpreter and an
owned CODEX_HOME for all compatibility tests. Explicit UTF-8 file IO/decode;
Windows utf8Mode0/gbk/cp936, no PYTHONUTF8/PYTHONIOENCODING. No dependency install.

- final-launch.xml:21 passed (owned processes/provider, pin/thread proof, ACL,
  create/restart/fork, cancellation, save failure, generation/epoch, forgery).
- final-host.xml:82 passed (host/official provider source and admission contracts).
- final-lifecycle-compat.xml:149 passed (Codex adapter, legal-offline gates, prompt
  spawn compatibility). No whole-repository suite run.
- final-http.xml:1 passed (owned FastAPI spawn/restart/Kill + child provider hits).
- Total253; failures0/errors0/skips0. All checks/compile/diff-check exit0.

Preserved exploratory evidence: first.xml exit1 (3 failures/6 passes): strict
wrapperPid check revealed venv launcher/interpreter split before provider hits.
Fixed via authenticated interpreter birth/ancestry + Job enrollment, not relaxing
to a bare PID. An intermediate test used wrong executionOperations disk key;
corrected to existing execution_operations without changing product serialization.
fixed.xml/lifecycle.xml/expanded.xml/pinned-options.xml/thread-pin.xml/pre-freeze.xml
are exploratory passes, not added to final253. No assertion skip/xfail added.

## Cleanup / untouched boundaries

http-evidence.json records server PID, fixed launch bindings (all process birth
identities), /provider-a then /provider-b hits, no private configs remaining,
server stopped and both HTTP/provider ports released. cleanup.json rechecks
recorded process identities; exploratory-cleanup.json found0 lingering owned
fixture processes after earlier experiments. Kernel Job zero-member assertions
and retained-handle closes are part of every successful launch cleanup.

No actual account/provider query/switch, user Worker operation, paid model turn,
real profile/history write, protected8767/8768, shared dependency modification,
React change, other worktree/main/practical/integration move, push or release.
Only the declared owned HTTP/Python fixture Workers were restarted/killed.
Stop for MA review; unsupported production and human-acceptance boundaries remain.
