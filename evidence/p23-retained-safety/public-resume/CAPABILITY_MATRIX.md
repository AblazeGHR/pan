# P23 public cancellation capability evidence (2026-10-27)

This supersedes the original P23 matrix's missing public connection row. Uses
existing `processOwner`; no new platform registry capability is introduced.

| Operation | Windows native Python 3.14 | Windows isolated venv Python 3.14 | Linux / macOS |
|---|---|---|---|
| Public Run cancel -> original owned cleanup | Real random-port HTTP, production runner, Job Object and FILETIME | Same; launcher and actual supervisor/root verified separately | Not run here |
| Unknown -> query/cold recover -> explicit public recheck | No kill on GET/recover/replay; only a new request authorizes revision 2 | Same | Not run here |
| Default pending -> uncertain -> original receipt reconcile | Same Attempt/Run/effect, no resubmit; cold interpreter reopen | Same | Not run here |
| Cleanup completion | Complete owned tree proof AND actual supervisor exit required | Same, including redirector ancestry | Not run here |
| PID reuse fence | Real live PID, deliberately mismatched raw FILETIME rejected | Same | Not run here |
| Product default detached executor | Real owned process tree, terminal lookup, supervisor exit, no second legacy lifecycle | Same | Not run here |
| Unsupported `processOwner` | Protocol and HTTP 501 negative tests; no mutation / no success fallback | Same protocol subset | No platform success claim |

`product_process` retains its original immutable publication and cancel=false.
The additive `product_process_cleanup_v1` release advertises cancel/reconcile=true;
its provider digest is `digest("pan.typed.process-cleanup.v1")`, generation 2.
Old frozen plans remain byte-for-byte unchanged. Platform-specific imports remain
behind the existing platform API; the new supervisor exit query uses that API.

19 existing skips are retained: 18 POSIX ProcessOwner nodes and one Windows
constructor-on-POSIX gate. See the exact node/reason list in validation.json and
backend-delivery.xml. No Linux/macOS execution, three-platform import/collection,
or remote CI is claimed. Actual OS PID-number recycling was not forced.
