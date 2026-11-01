# JOB-RETIRE revision26 — owned QQ loop lifecycle and approved unknown API rejection

Same Session/tree/branch, baseline `f905ff36726aa395f5edb176781f3db467a03a0d`. QQ test repair is **`9ccf4f2184851a7a67f60823b0c733d47d1386e6`**. Policy-only dedicated tests, added only after that Job's terminal notice, are **`38b60e270c060a086705cbd5eb0b149f3d75441f`**. Product source is identical to baseline; no publication, retry, transport, Worker, shared fixture or migration script changed.

## Root cause and exact repair

MA's combined run (`dc8b1ff8`, Job `f80413797002fc90d4e93327`) reported 95 tests with 5 failures and 3 errors, concentrated in unchanged r10 manual QQ tests. Original XML remains at `D:/project/Pan/.workflow/job-unification-review-20261028/ma-rev25-combined.xml`; its hash is recorded in `job-start.json`. The failure is preserved, not converted into a production unknown-success assertion.

The r10 tests repeatedly called `asyncio.run` while the actual host sender pools one `httpx.AsyncClient`. A first call binds pooled connections to its loop; the next call uses a different loop after the first has closed. Successful retries could therefore become uncertain before owned OneBot received any call, and client teardown could raise `Event loop is closed`. The earlier standalone eight-node green did not prove same-process order isolation.

Only `tests/test_retirement_rev10_qq_retry.py` changes: `run_qq(qq_http)` creates one `asyncio.Runner` and an actual pooled HTTP client for the whole test. Every asynchronous dispatch, cold-service close, spoof/provenance call and lookup pulse in that file uses the helper. Explicit fixture dependency orders setup as HTTP fixture → Runner/client and teardown as client `aclose` on its original live loop → Runner close → HTTP listeners/outer fixture cleanup. New finalizer assertions check those boundaries. The r9 fixture itself did not need modification. No global asyncio monkeypatch is used.

All **42 original assertions** are preserved by per-function UTF-8 AST multiset comparison, including duplicate assertions. The real plugin, OneBotChannel, NoneBot and owned HTTP endpoint remain in the delivery chain. The actual fixture-only client uses timeout30s and `trust_env=False`, following the owned r25 fixture: this avoids local client/NoneBot startup and proxy overhead for loopback proof. It does not change product timeout5s, classify unknown as no-send, weaken assertions or establish production latency acceptance.

## Immutable validation and resource ownership

Both QQ gates ran serially in durable Job `job_c0d1f1b3b9b152b369291947`, targeting this Session. The system terminal notice reports completed/exit0. No polling or running-HEAD merge occurred.

| Gate | Immutable source | Actual result | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| Original r10 manual QQ | `9ccf4f21` | 8 passed, 0 fail/error/skip | 0 | 81.46 |
| Same pytest process, runtime QQ 23 then r10 8 | `9ccf4f21` | 31 passed, 0 fail/error/skip | 0 | 312.88 |
| Unknown API decision successors | `38b60e27` | 5 passed, 0 fail/error/skip | 0 | 2.45 |

The mixed JUnit execution order is explicitly checked: first all 23 runtime QQ nodes, then all eight r10 nodes. This includes cold retry/default/selected/unknown bot, duplicate requestKey/CAS, successful original message_id and raw text, post-call ambiguity, spoof/non-2xx/provenance negatives, runtime hard crashes and no-replay. It reports **31 distinct QQ nodes**; standalone r10 is a deliberate repeat, not eight additional distinct proofs. Policy adds five distinct nodes: **36 distinct nodes, 44 total executions**. Teardown errors are zero in both QQ gates. Fixture close assertions run before outer HTTP teardown; resource observation is recorded separately.

Commands use `D:/project/Pan-main/.venv/Scripts/python.exe` through `scripts/run_tests_isolated.py`. Raw commands, node order, counts, duration and XML/log hashes are in the three `*.command.json` files. Source/test immutability was checked before/after the QQ gates. The later policy commit adds one dedicated test only; QQ product and test source remains byte-identical to the tested commit. No 351-node repeat, build, remote CI or full MA combined suite was run for this test-only repair. Candidate green is not combined integration green.

## User decision: unknown old action.api

Authority: `.workflow/job-unification-review-20261028/UNKNOWN_API_POLICY_AMENDMENT_20261031.md`, decision **`job-unknown-api-reject-approved-20261031`**. Unknown old `action.api` must explicitly error, with no implicit assign. Only ordinal9's force-written unknown API → assign expectation is cancelled. It is no longer a pending decision or fallback to restore; legal assign/send/QQ/shell/arbitrary command capability is not cancelled. Old tests, original assertions and raw reds remain unchanged.

The five policy successors reuse current normalizer/reference validation: two exact unknown API values reject; actual canonical POST/PUT with an unknown legacy field rejects `invalid_schema`/400 and unknown immutable Action reference rejects `reference_conflict`/409, with definition/operation counts and stored Job unchanged; a complete synthetic unknown scheduled source retains exact bytes/hash through read-only inventory and cold canonical startup/tick with no implicit assign, queue, Run, Attempt, effect or Worker. Inventory alone is not the execution proof. The original source record remains non-executable, not migrated. Existing known-API normalizer and canonical references suffice; there is no new dispatcher/error engine/ledger.

`current43-matrix.json` is a new versioned delta over r25, preserving all 43 original assertion fields and historical provenance. Only ordinal9's policy disposition is changed by this decision; the current QQ order evidence is attached separately, other rows are not relabelled newly accepted. The original r6 manual-denial red assertions remain preserved as explained in r25.

Development XMLs are retained: first missing owned `env` fixture import, then corrections to the new test's existing history/report API field assumptions and exact reference error status. They are test-authoring failures, not modified old assertions or newly fixed product behavior. No migration owner files were edited.

## Remaining acceptance boundaries

MA must apply/review this test-only candidate and independently rerun its combined integration. Remote CI, other platforms, production five-second latency, real QQ traffic, profiles/51/60, actual migration activation and deployment are unverified. The new QQ runtime release handoff from r25 remains available to migration owner; this batch does not claim mapper activation or full old-capability migration. No main/practical movement, push, existing services/8767/8768 or real account/data operations occurred. Unknown API fallback is no longer listed as an unresolved design conflict.
