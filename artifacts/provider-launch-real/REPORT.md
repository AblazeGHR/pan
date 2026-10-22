# Installed Codex fixed-provider compatibility candidate

- Tree: `D:/project/pan-worktrees/event-trigger-core-20261022`
- Branch: `feature/event-trigger-core-20261022`
- Accepted baseline: `19c294a55d19a3d4fed07833f5715221ac91cf45`
- Exact tested source: `a0875956503f957770b0fd071933066543e1c568`.
- A later evidence-only commit contains this report and logs; no source changes
  follow the tested SHA. This is a TA candidate, awaiting MA acceptance.
- Installed CLI: `codex-cli 0.163.0`, invoked through
  `D:/node_npm/node_global/node_modules/@openai/codex/bin/codex.js` and the real
  `packages/core/adapters/codex/wrapper.py`. Retained Job-member image receipts
  include the installed `codex-win32-x64/.../bin/codex.exe`, not the native fixture.

## Reproduction and minimal repairs

The real-wrapper test failed before repair with `Fixed provider launch was not
confirmed` (exit1, before.xml). before-handshake.json/before-diagnostic.xml show
that the installed CLI's temporary-home PATH-alias warning arrived where the
parent expected its signed started frame. The owned offline native probe also
captured config/read's actual nullable ProviderConfig fields and false capability
defaults; the previous strict export projection rejected this shape even though
all credential fields were null. native-probe.json preserves that response;
tests/fixtures/real_codex_0163_provider_config.json preserves exactly its selected
provider fields, with provenance beside it. Failure XML original bytes are preserved beside the readable XML as .raw.gz;
readable XML only normalizes trailing whitespace for Git. Neither response was fabricated by
the native fixture. The initial probe did no turn and used loopback port 1.

Repairs are limited to the real compatibility differences:

1. Separate native response normalization accepts only the observed null fields
   and two exactly-false capabilities. Nonempty credential/header/env/catalog/
   retry extensions, unknown fields and incorrect/missing identity still reject.
   The trusted export grammar is unchanged and rejects these extra fields even
   if null. No native/default provider identity is inferred.
2. The opted-in wrapper buffers startup diagnostics/notifications at 64KiB max
   while the trusted attester emits the authenticated receipt sequence. It
   flushes after confirmation; overflow cannot confirm. Parent signature/order/
   identity/generation/config/thread validation is unchanged. Diagnostic events,
   including a receipt-shaped lookalike, cannot bypass the buffer.

## Executable final verification

PowerShell, in the core worktree, with the source above checked out:

```powershell
$env:TEST_REAL_CODEX_JS='D:/node_npm/node_global/node_modules/@openai/codex/bin/codex.js'
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/verify_provider_launch_real.py
```

The verifier calls `scripts/run_tests_isolated.py` for every pytest group; its
complete argv, durations, source SHA and exits are in verification.json. Text IO
and decoding are explicit UTF-8. Actual final encoding: utf8Mode=0, locale cp936,
PYTHONUTF8/PYTHONIOENCODING unset. No dependency installs or shared cache writes.
All commands were short targeted runs, not a full repository suite.

| Final check | Tests | Failed/errors/skipped | Exit | Evidence |
| --- | ---: | --- | ---: | --- |
| Installed CLI/wrapper/provider | 1 | 0/0/0 | 0 | real-installed.xml/log |
| Strict native shape, output buffer, launch lifecycle safety | 44 | 0/0/0 | 0 | launch-regressions.xml/log |
| Codex adapter/wrapper and metadata-only large resume | 78 | 0/0/0 | 0 | wrapper-regressions.xml/log |
| Diff check from accepted baseline | — | — | 0 | diff-check.log |
| Targeted compile | — | — | 0 | compile.log |

123 tests passed. Prior 253/MA fixture results are not counted in this batch.

## Actual native coverage and cleanup

real-cli-evidence.json contains redacted-of-signatures handshake frames, safe
receipts, three durable binding operations, and retained native Job-member PID/
birth/image/termination evidence. No private capability or export config body is
in the binding ledger. Only the test's trusted producer and local Responses API
are injected; the CLI/app-server/wrapper/config reader/thread implementation and
history/state writer are real.

- config/read passes the native projection attester.
- thread/start and thread/resume return the required unique process modelProvider
  alias; resume retains the exact native thread ID.
- Four successful actual Responses requests use `/a/responses`, `owned-model`, no
  Authorization header, on owned loopback port 62603. There are no requests to
  the alternate `/wrong` global endpoint.
- A deterministic request barrier holds the real first request at our endpoint
  while the test changes its owned global provider config. Subsequent request,
  next-process resume and branch remain fixed. Each process has a fresh alias,
  receipt and durable binding; host inventory maps it to provider-a independently
  of the unrelated current global-provider ID.
- Existing SessionsProvider.fork_session materializes a branch from the real
  CLI-created native state/rollout. A new actual CLI resumes the new branch ID
  and successfully uses the pinned local provider. Native indexing is awaited
  through a bounded test-only check; no native DB/history rows are synthesized.
- Product Pending.environment retains CODEX_HOME. The test alone supplies a new
  empty profile and history sentinel, scrubs inherited auth/proxy/config inputs,
  and selects file-only credential storage; it does not copy/read user auth.
- All three launch Jobs are retired; independently retained member handles
  (including Rust codex.exe) report dead. All retained probe handles close,
  private launch directories are removed, ownedProcessesRemaining=[], final
  privateConfigsRemaining=[], ownedProfileRemoved=true. Provider thread stops
  and providerPortReleased=true. No protected port was accessed.

Early failed/development pytest trials used owned temporary profiles outside the
worktree. A proposed extra sentinel-checked recursive deletion command was rejected by
automatic approval review (`blocked by policy`, no more specific reason); it was
not executed and was not retried via another deletion mechanism. The final read-only
inventory in trial-residuals.json found none of the earlier trial profile paths
remaining (empty list); no deletion mechanism was retried. Final frozen
verification's own profile/process/port cleanup above passed independently.

## Unfinished / unverified boundaries retained

- This proves credentialless fixed endpoint compatibility with installed CLI
  0.163.0. It does not prove official credentialed-provider export/pinning,
  keyring/OAuth rotation or compatibility with every future CLI version.
- No production trusted immutable exporter is installed. Existing default-off
  flags and host admission remain unchanged; ordinary production Workers stay
  unbound when no trustworthy source exists. Unknown never becomes verified.
- Native branch materialization plus spawn/resume is covered. Full production
  Pan branch HTTP/UI lifecycle, native-control kernel, compressed/reverted
  lineage variants and a production Browser session were not exercised here.
- No new task continuation, account/model switch, worker restart/wake policy
  effects, crash-orphan auto-deletion or real-account quota queries are enabled.
- Current source/API migration: none; input schema and persisted receipt format
  remain compatible. New native response extensions fail closed pending review.
- No React/external adapter changes, real profile/Worker/provider operations,
  paid model calls, 8767/8768 access, main/practical/integration movement,
  push/release or dependency mutation. Overall human/MA acceptance is pending.

Stop at this batch; await MA review.
