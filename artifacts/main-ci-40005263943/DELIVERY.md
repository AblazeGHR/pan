# Python3.12 peer-reset close-tail compatibility

Exact remote source86b03a2a83798239a1deb3a8beabc87ce3935ddb. Automatic run40005263943 attempt1 concluded failure. Original unique collector job_96a1033dc47454d070d1c2b5 completed exit0; every download succeeded on its first bounded attempt, collection-errors=[]. Complete job/run logs, check annotations, metadata and original JUnit ZIPs are retained here; collection-and-red-logs.zip also contains the exact failure traceback and deterministic red experiment.

| Python | Job | Conclusion | Total | Passed | Failed | Skipped |
|---|---|---|---|---|---|---|
| 3.12 | 120591443361 | failure | 5571 | 5509 | 1 | 61 |
| 3.14 | 120591442956 | success | 5571 | 5510 | 0 | 61 |

Only3.12 failed tests.test_user_locker_browser::test_locker_browser_http_mcp. All browser/product assertions completed, then the owned process exceeded the unchanged20-second graceful-exit wait, forcedTerminate=true, exit1. Its full log again shows ConnectionResetError WinError10054 at stdlib Proactor _call_connection_lost -> socket.shutdown; Uvicorn connection set is empty and its host task is waiting on asyncio.Server.wait_closed before lifespan shutdown. Missing owned provider-home/.codebuddy/projects warnings remain concurrent background retries, not proof of this shutdown blocker. Independent /jobs passed in both legs; it remains unlocated, not declared fixed.

Both test identity multisets match and both61-skip multisets match the original baseline, with no additions/removals. Original14:3.12 has13 passed and Locker-browser failed;3.14 has14 passed. All identities/outcomes and full skip multiset are in gate.json. This evidence applies to86b03a2a, not the follow-up candidate's remote gate.

## Root cause and minimal correction

The previous recovery was only exercised against the modern asyncio Server._detach(transport) shape. Inspection of the locally installed CPython3.12.12 stdlib confirms Server._detach() takes zero arguments and decrements _active_count, while3.14 tracks individual transports and requires that argument. The remote3.12.10 traceback is at the same stdlib shutdown path; unlike3.14 it has no _clients diagnostic set. Under the old recovery, socket.close succeeds but _detach(transport) raises TypeError; the handler falls back to the original error handler and the listener activity count remains held. Thus the earlier repair was incomplete for the other matrix version. The counter/detach API difference, not the usage warning, explains this additional repair.

A new regression case supplies the exact zero-argument3.12 detach contract to the same real stdlib Proactor callback/error fixture. Before the correction,7 passed/1 failed,exit1, with the TypeError at server._detach(transport). The modern variant still passes. The fix inspects and binds the bound detach signature before any socket/state mutation, selects either the known one-argument or zero-argument form, and calls detach once. Unknown call shapes return false without closing anything. Existing ownership, exact callback/traceback, already-closing and drained-I/O gates are unchanged; no retry after a failed detach is introduced. The owned test-only shutdown diagnostic now also prints the3.12 activity count without dumping buffers.

## Validation and limits

- Canonical Python D:/project/Pan-main/.venv/Scripts/python.exe:tests/test_host_peer_reset.py,8 passed,0 skipped,exit0 (peer-reset-312-green.xml). Covers both detach API contracts, exact one close/detach, no duplicate protocol callback, foreign listener, pending read/write, non-closing/unrelated errors, and handler restoration.
- Durable Job job_d99e40595b246aae2928b919:isolated wrapper, exact Locker browser identity plus the peer-reset file only,9 passed,0 skipped,exit0 (targeted-green.xml). No unrelated suite was run. Real browser/HTTP/fakeCLI completed; server graceful exit0,forcedTerminate=false, random port57490/listener closed; post-run read-only command scan found no process referring to owned pytest-4074. See owned-cleanup.json and owned-http-evidence.zip.
- The zero-argument shape was modelled under canonical3.14 and compared with installed3.12 stdlib source; this is not a claim that the full new candidate ran locally on3.12. Its later exact-SHA remote matrix remains required. Neither test timeout nor production shutdown budget changed. No assertion removed/weakened or skip/xfail added.
- The private asyncio shape dependency is explicitly checked before mutation; future unknown implementations remain visible failures. This changes only host connection cleanup, no persisted schema/format, Worker owner/generation/lease, QueueID/unknown non-replay, Pause/Illegal, Locker trust binding or Job snapshot behavior.
- Independent /jobs, historical settings500 and trace10-row unique-cause limits remain open as previously recorded. Earlier accepted private data-copy gates were not rerun or relabelled; no data/profile tree was touched.

Work remains in the original Session/worktree. No new collector, TA push/dispatch/rerun/release/account switch, main/practical movement, message-UI-tree access, shared dependency write, real service/provider/account/QQ or real-profile operation. Candidate awaits MA acceptance and subsequent exact-source remote CI; gate is not declared green.
