# Exact MA interpreter recheck

This supplements `../public-resume/CAPABILITY_MATRIX.md`. The platform capability
remains `processOwner`; this recheck changes no platform or product code.

| Boundary | Windows system Python | Windows Pan-main/.venv Python | Linux / macOS |
|---|---|---|---|
| Launcher and real supervisor | Separately capture and verify native PID/birth; if different require actual ancestor relation | Same; venv redirector is expected to differ | Not run |
| Business owned root and script interpreter | Separately capture and verify native PID/FILETIME; if different require actual ancestor relation | Same | Not run |
| Cleanup confirmation | Confirm original identity, complete owned tree proof, and actual supervisor exit | Same | Not run |
| Unknown/reused identity | Unknown never authorizes cleanup; stale FILETIME on real live PID is refused by native owner | Same | Not run |

`execution_host.supervisor_exit_evidence` has a narrow responsibility: query exit
of the original trusted supervisor identity. It neither kills nor grants kill
authority. A native dead+ours=true result confirms exit; a live PID with a proven
birth mismatch establishes that the original process exited. If native reopen
cannot prove status, proven PID absence is an exit postcondition only. An alive
original, access denial, incomplete identity, or other unresolved status does not
confirm exit. The original execution document is fenced by job/effect and all six
Attempt identity fields before its identity/proof is consumed.

The real delayed-supervisor test blocks cancellation completion after the
business tree has exited and permits it only after the supervisor exits. The
five missing-proof protocol negatives remain distinct from the real tree tests.
No PID-number recycling was forced; a deliberately wrong FILETIME is a native
negative fence test. No POSIX or three-platform CI success is asserted.
