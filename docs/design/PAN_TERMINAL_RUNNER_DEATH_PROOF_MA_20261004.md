# Runner death proof: Python shim boundary

## Measured defect

At the `2b54b7eb` baseline, an owned Popen wrapper with spawn PID 41 and exit
code 0 yielded `exited=true` even when the independently matched bootstrap
runner PID 42 / FILETIME 9 was ALIVE. UNKNOWN also yielded true. This contradicts
the service's three-proof close contract. It is a same-user local process-layout
issue, not a remote authentication bypass. Engine/runner cleanup reports alone
must not replace the separate process-death proof.

The new gate set first recorded **10 failed / 4 passed**
(`shim-identity-pre-fix.xml`). The pre-fix boolean cases that already rejected
their specific values are recorded as passed, not invented failures.

## Direct repair

* An original Popen process provides runner death proof only if its spawn PID
  exactly matches the bootstrap runner PID with a valid recorded identity.
* For production Windows interpreter shims, startup opens and retains the real
  runner query/synchronize handle, verifies exact PID/raw FILETIME and ALIVE, and
  retains it before completing secret/bootstrap startup. Failure remains unproven.
* The retained handle's same-object signaled wait is the death proof. ALIVE,
  UNKNOWN, wrong PID, wrong FILETIME, exceptions, and a busy handle owner reject.
  Probe/release are nonblocking and serialized against one another.
* Close and reconcile release the extra handle only after the normal death and
  cleanup conditions. CloseHandle failure preserves the same object and retry
  state, without deleting the secret. Object GC only releases the query handle;
  it does not terminate the runner or fabricate a terminal state.
* Identity fields reject boolean/float coercion and overflowing decimal parsing.
* A wrapper exit remains a diagnostic in reconcile, but is never runner death.

The old service fake Popen default PID 50000 disagreed with its bootstrap PID
40001. It is corrected to 40001; all behavior and cleanup assertions remain.
The initially run neighboring selection records this mismatch's failures in
`shim-service-initial-direct.xml`. Subsequent fixed-fixture logical selection:
**107 passed / 8 deselected** (`shim-service-fixed-direct.xml`).

## Verification and limits

Latest direct identity/cleanup gates: **19 passed**
(`shim-identity-final-direct-v2.xml`). Real Windows kernel control releases the
original owned Popen handle after child exit; the independent retained runner
handle still proves exact-object death and closes successfully. Another gate
requires credentials to survive a false CloseHandle result and confirms that a
same-owner retry finally closes/deletes correctly.

Production service real paths plus identity selection under uv:
**25 passed / 77 deselected**, 94.04 seconds (`shim-service-real-uv.xml`), including
the six real service tests. The previous intermediate 18-gate uv result remains
separate evidence (`shim-identity-final-uv.xml`). No secret, provider account,
foreign process, or existing Pan/8768 service was used.

The earlier frozen whole repository at `4823fc0e` remains **2581 passed / 10
skipped**; it predates this repair and is not claimed as its full-suite result.
Arbitrarily late UNKNOWN after every verifier handle is gone still does not mean
DEAD. Cross-platform, cross-user and all-build OS failure behavior remain outside
this local measurement. No main/practical advance, deployment, push, or restart.
