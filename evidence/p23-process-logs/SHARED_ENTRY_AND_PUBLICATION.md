# Method ownership and publication agreement

Baseline: `67d125de794132f72011012d8c6814110aafe8ef`, fast-forward from
`ebbf54e26489091a227115316eecf81c10cf2470`; all prior safety work is ancestry.

MA explicitly authorized only these shared additions:

- `FinalService.read_process_log(run_id, attempt_id, *, offset=0, limit=65536,
  actor)`: existing job read grant, trusted live/archive aggregate locates the
  original Attempt/effectKey, no caller Job/effect/path/reference, unlock before
  external read, grant rechecked before returning data.
- `CanonicalService` matching async protocol and
  `GET /job-runs/{run_id}/attempts/{attempt_id}/log`: job read authorization,
  one decimal offset/limit each, no extra fields (including path/delete).
- Later MA authorization adds `FinalService.process_log_references` and
  `GET /job-runs/{run_id}/logs`, using existing `_page` principal/profile/Run
  cursor scopes and limit<=100. `_process_log_records` is the one shared
  live/archive locator for these two new methods. Ordinary create operations
  lacked Attempt IDs, so this discovery is required for a public-only flow.
  Original pending receipt or original execution records supply references;
  legacy effects without one report unavailable. No path is fabricated.

All other methods and existing routes are unchanged, including publication,
pin/coordinator, dispatch, cancellation, reconcile, and receipt settlement.

The separately owned `_install_product_actions` delta was acknowledged by
Session TA `ses_37b685e9873419e6` and approved by MA:

1. New `product_process_logs_v1`, copied from the immutable definition of
   `product_process_cleanup_v1`.
2. `providerRef={id:"pan.typed",
   releaseDigest:fc.digest("pan.typed.process-logs.v1"),generation:3}`.
3. Original outputSchema object gains optional `properties.logRef={type:"object"}`;
   no new required field. Actual process receipt has `receipt.logRef`.
4. Input schema, requiredCapabilities, effect, execution, cancel, reconcile,
   process-exit schema and terminalProofSchema remain unchanged.
5. Old release identities/frozen plans remain unchanged. Repeated publication
   must be idempotent. This P23 candidate does not edit that method or merge the
   Session candidate; MA performs method-level combination and acceptance.

Production runner's existing `RecordingOwner.spawn` supplies the controlled file
over TypedExecutor's DEVNULL arguments. The typed executor itself and bootstrap
diagnostics were deliberately left unchanged with MA approval. No custom/self
reported provider receipt is rewritten to insert a log reference.
