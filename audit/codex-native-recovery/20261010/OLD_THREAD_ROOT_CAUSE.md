# Old Codex thread init overflow — 2026-10-10

Parent: `9b0ed940`. This is a separate follow-up to the already merged TUI
buttons/navigation batch; the earlier report's original deployment boundary is
historical and is not rewritten.

## Confirmed cause

`AppServer._emit_thread_started` forwarded the entire resumed thread (including
turns) to Worker stdout. Worker caps each newline-delimited frame at 256 KiB;
the resulting truncated JSON is ignored by `CodexAdapter.parse_event`.
`native_kernel_ready` therefore remains false even when the kernel resumed.
This explains an old-history initialization failure, not every possible switch
failure. The supplied live Session had already lost its Worker during an
external service restart, so it was not mutated or repaired in-place.

The real affected rollout was copied into a temporary, private CODEX_HOME.
Only synthetic placeholder credentials were seeded; original auth/config were
not copied. No turn was submitted. Inference was directed to unused loopback
port 1. Before the fix, the resumed init frame was **1,059,315 bytes** and failed
Worker's bounded frame parsing. Parsing the full unbounded frame separately
allowed a native receipt, demonstrating the framing boundary rather than a
failed kernel resume.

## Minimal repair

Emit only `type=thread.started` and `thread_id`. Both existing Codex consumers
already use this identity shape. Keep the complete `_thread_resume_info`
independently for resident TUI restoration; no source thread/history mutation,
no global stdout-cap increase, no synthetic ready flag, no replayed queue.

## Verification (direct isolated commands)

- Corrected regression harness on unchanged production: **1 failed**, specifically
  `bounded stdout discarded resumed init JSON` (event was None). The harness's
  initial wrong `_iter_stdout_lines` argument and strict UTF-8 decode were fixed
  before attributing the product failure; production uses replacement decoding.
- After fix, affected owner/completion/transport/conversion/real-kernel collection:
  **42 passed in 6.11s**.
- Expanded final collection: **146 passed in 10.83s**, no skipped tests. Files:
  `test_codex_resumed_init`, `test_codex_adapter`, `test_codex_worker_integration`,
  `test_codex_takeover`, `test_native_control_api`, `test_codex_control_owner`,
  `test_codex_native_completion`, `test_codex_native_transport`,
  `test_codex_session_conversion`, `test_codex_native_kernel_real`,
  `test_premerge_legacy_data`.
- New regressions assert the actual Worker framing/parser, ready transition,
  duplicate-init suppression, retained CLI id, unchanged queue/history and full
  resume object retention.
- Real old rollout copy: source **4,303,848 bytes**, init **77 bytes** at 0.884s;
  bounded production Worker parser accepted it. Native receipt **1.090s**, true,
  generation 1; Pan receipt **1.091s**, true, generation 2. Wrapper had not exited
  before cleanup. Source SHA-256 comparison unchanged. Owned Job cleanup, reader
  joins, handle close and temporary directory removal converged.
- `git diff --check` passed. No frontend change/build required in this follow-up.

These are tool-run observations, not claims that raw pytest logs are attached.
No private rollout or history contents are stored in this repository. The
read-only-copy probe is `probe_old_thread.py --rollout <owned rollout path>`.
Tests use `scripts/run_tests_isolated.py`; the probe has a 35s observation bound
and owned-Job cleanup. No original Session, provider or existing service touched.

## Remaining deployment boundary

No full Python repository test, production Session mutation, service restart,
push or branch integration in this follow-up. Existing wrappers must be
restarted/recreated to load the changed emitter; refreshing frontend assets
alone cannot update a running wrapper. User history and pending queue should
remain preserved by normal lifecycle operations, not a destructive new thread.
