# Repository integration regression

The full `tests/` uv selection completed: **2558 passed / 9 failed / 10 skipped**,
596.61s. Original XML is `audit/terminal/implementation/browser/full-repository-uv.xml`.
It is a failed run, not a claim that the repository passed. The run started at
`23e790c1`; independent lifecycle work was completed while it ran, so it is not
represented as an immutable final-HEAD certification.

Six failures were older test expectations already fixed in frozen main
`e6a5e84e`: backend history message IDs (one), worker history identities (three),
Codex ancestor/descendant directory matching (one), and POSIX Windows-prefix
handling (one). The four test files have been synchronized byte-for-byte with
that main snapshot using reviewed patches. No corresponding production files
changed; the explicit message-identity assertions were restored, not dropped.

The Terminal route-registration test assumed the global Pan app had never run
its lifespan. Earlier tests left a real `closing` runtime, producing the correct
503 closing rather than the assumed 503 not-ready. The test now scopes
`app.state.terminal_runtime=None` with monkeypatch and restores the prior owner.
It still checks the real app/router and exact no-runtime response.

The remaining two failures were an incremental-save timing threshold (19.380ms
full vs 9.840ms incremental, narrowly below 2x) and a Windows `socketpair()`
WinError 10055 while constructing an asyncio loop. No performance assertion or
source-validation contract was relaxed, and no underlying cause is claimed
beyond those measured failure facts.

All seven affected test files subsequently ran together: **185 passed**, 15.22s,
`full-failures-targeted-final-uv.xml`. This verifies the repaired expectations and
shows the two transient failures did not recur in that run; it is not a full
repository rerun or a guarantee against resource-pressure/timing failures.

Tests use isolated session stores, deterministic fake CLI providers, and a
fresh loopback test port, never the existing 8768 service. No main/practical
branch, remote push, provider account, or deployment was changed.
