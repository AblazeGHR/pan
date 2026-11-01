# CP-X1 lifecycle closure

Candidate `bdbe76f1b4d56b83eae3882a5b06304ff70b2fab` adds only owned lifecycle
tests, owned runner probes, and the platform-fast gate entries. The original
assertions and the historical red XML remain unchanged in
`evidence/cp-x1-lifecycle-r14/original14.xml`.

The machine-readable record is
`evidence/cp-x1-lifecycle-r14/node-closure.json`. It maps the 14 exit/restart
records, seven genuine-main owned-host records, and 11 controller records to
their exact canonical successors. It also records two additional canonical
runner outcomes for the old three-generation exit/exception behavior.

The bounded current-source checks were:

- 36 canonical preparation, nonblocking, prelaunch-fence, and strict-successor
  tests passed (`canonical-policy.xml`); the same preparation and strict files
  are now explicit entries in `scripts/platform_fast_gate.py`.
- 2 canonical runner tests passed (`canonical-tree-exit.xml`). Root exit is a
  succeeded durable receipt with stdout/stderr and confirmed tree cleanup. An
  injected owner wait error remains `unknown` with the original error and one
  outbox event while the owned tree and desktop are cleaned. This intentionally
  does not relabel the old `failed` assertion as equivalent.
- 8 controller/observation tests passed on real canonical identity and named
  venv paths (`controller-strict.xml`), and all seven owned-host genuine-main
  HTTP handshake records passed.
- 1 genuine isolated main restart passed with a new PID/birth, durable original
  effect identity, offline choice, readiness, and port release
  (`genuine-restart.json`). It does not claim an active-worker restart.
- 6 owned creation-edge probes passed across base and named venv launchers;
  these are platform/native evidence and do not substitute for the canonical
  ExecutionStore attempt proof.

The remaining open items are explicit: legacy registry/task-name/argv
observations are retired or fixture-only; the three legacy controller records
remain open for CP-X2's private desktop/atomic creation combination; and the
canonical wait-error result is an unknown-safety semantic difference requiring
acceptance or a separately authorized product decision. Signal variants outside
the seven listed owned-host methods are not claimed. Remote CI was not rerun by
this TA; MA owns non-main integration and remote validation.
