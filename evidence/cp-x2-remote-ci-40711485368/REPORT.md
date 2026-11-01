# CP-X2 remote CI 40711485368 diagnosis batch

Source of truth: local immutable artifacts under
`D:/project/Pan/.workflow/job-unification-review-20261028/remote-ci-40711485368/`.
No remote rerun, no waiting, no service or data operation. Run head SHA:
`3faef66ce064c62dd53d45b2f0286a3df880a91c`.

## Owned POSIX/Darwin scope

The macOS platform-fast job used Python 3.12.10 and exited 0 after import,
collection and fast tests. The Ubuntu platform-fast job used Python 3.12.15 and
also exited 0. The CP-X2 proof files were read individually; no Windows result
was used as target-platform evidence.

macOS identity/ABI proofs:

- public native identity and cleanup use `kind=proc_uniqueid`, versioned
  `lifetime-v1:<uniqueid>:<start_sec>:<start_usec>` birth and an exact boot UUID;
  original identity verification is true, owned tree is confirmed, three
  generations are gone, supervisor is reaped, and both executor/canonical ports
  are released;
- ordinary successful large Unicode log run is 196013 bytes, merged streams and
  exact Unicode are true, original fence/history archive/cold reopen are true,
  effect count is one, and cleanup is confirmed;
- framework-launcher proof node is present and passed with no execution payload;
  the actual ABI/native source references and line-level evidence remain in the
  earlier CP-X2 report. This artifact alone does not add a new ABI claim;
- leader-first public and large-log cases remain `unknown` with original query
  available and `effects=1`; cleanup later releases ports and leaves no owned
  process/supervisor, while the cleanup proof intentionally remains unconfirmed.

Linux identity/leader/log proofs:

- starttime + boot_id identity is used;
- normal large Unicode log run is 196013 bytes, exact Unicode/merged streams,
  original fence, cold reopen, archive and supervisor/owned-tree cleanup all
  pass with one effect;
- leader-first public and large-log cases remain `unknown`, original identity is
  queryable, `effects=1`, and no survivor/port remains after the fixture's
  cooperative cleanup. `cleanupProof.confirmed=false` is retained for the
  leader-loss phase and is not converted into success.

The remote evidence therefore shows no owned POSIX/Darwin product failure to
repair in this batch. Unknown, leader loss, original identity, effect count,
port release and supervisor fences are preserved. There is no change to
`process_posix.py`, Darwin identity, process logs, or any shared executor code.

## Full pytest failures and ownership triage

The full Windows pytest jobs (3.12 and 3.14) failed with the same broad
non-CP-X2 categories. The owned-scope subset is only the retired/legacy
lifecycle and Windows hidden production nodes; POSIX tests are explicitly
skipped on Windows and cannot be repaired from this run. Most failures are
background-job/API retirement, scheduler/QQ, settings, quota, web/API
compatibility, host-signal, and unrelated lifecycle integration. Exact node
and first diagnostic text are preserved in `pytest-3.12-failures.json` and
`pytest-3.14-failures.json`; the original full `pytest-results.xml` remains in
remote artifacts.

No CP-X2 source fix or test weakening is justified by this CI run. The remaining
CI-level gate is a future target-platform/full-matrix run if MA needs broader
coverage; this batch supplies diagnosis and preserved evidence only.

## Evidence index

- `summary.json`: per-platform phase exits, proof summaries and identity kinds;
- `pytest-3.12-failures.json`, `pytest-3.14-failures.json`: exact full-run failure
  inventory, unmodified source artifact excerpts;
- `failed-steps.log`, `run.json`, `result.json`: original CI control evidence;
- `manifest.json`: hashes of this copied evidence batch.

No main/practical movement, push, remote CI operation, CP-X1/shared
`execution_host` edit, or deployment occurred.
