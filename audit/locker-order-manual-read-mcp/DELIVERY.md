# Locker incremental candidate delivery

Base: 03158ec517a98493e41b292b7f0575fefb49fe12
Branch: feature/locker-order-manual-read-mcp-20261024

Chronological DOM order uses existing createdAt/ID comparator for ordinary Messages and Cards. Default tail entry and a 48px follow zone mirror History semantics. Display pagination reveals the latest 50 then prepends 50 with height-delta anchoring. It uses existing fully fetched API cursor pages; hidden rows retain Card draft state. This is not server-side newest-first pagination.

No visibility observer writes read state. Explicit row read/unread and source Read all persist to original rows. Card submit/reject lifecycle remains unchanged.

MCP locker_message(create/get) derives source from local bound stdio caller and recipient from LOCAL_RECIPIENT. It reuses existing loopback caller transport constraints and accept_locker_message. Same source/messageId/document is idempotent, changed content conflicts, tombstones prevent resurrection. Unknown is lookup-only; get never resends. Unknown Message fields survive, including inert self-reported metadata that never overrides envelope source. Attachments remain source-scoped.

## Validation

- unit-python.xml: 36 passed, 0 failed, 0 skipped; exit 0. Command: python -m pytest tests/test_locker_message_producer.py tests/test_locker_messages.py tests/test_locker_trusted_binding.py tests/test_user_locker_lifecycle.py -q.
- frontend.xml: 21 passed, 0 failed; exit 0. LockerView and lockerModel.auth vitest suites.
- npm run build in packages/web: exit 0; TypeScript, Vite production build and 94 compressed assets verified. Existing dynamic import/chunk size warnings.
- live.xml: 2 passed, 0 failed, 0 skipped; exit 0. Job job_7eb44894c31d0d8b9c88ad7d, 41 seconds. Real stdio discovery/create/get/replay/conflict/source override attempts/foreign attachment rejection, real HTTP/UserAuth, production Chromium, manual read, cold service restart, Card trusted-choice lifecycle and fakeCLI exactly-once notifications. No core API interception.
- scroll/scroll-browser.json: 113 mixed rows; aggregate/source default tail, 50-row prepend anchor, non-tail new arrival preserves position, no automatic read writes, explicit read survives browser reload.
- before-manual-read.xml: same new observer/manual-only regression against baseline component fails (expected); current component passes.
- First live failure was invalid fixture Session creation; second scroll failure incorrectly expected row offset zero instead of preserving the existing Load older button offset. Both are retained as failure XML/log/screenshot. Corrected test measures actual before/after offset with 3px tolerance; no product assertion was weakened.
- Existing pytest timeout-plugin and dependency deprecation warnings are retained in logs; no skips added.

## Evidence and boundaries

Production screenshots: scroll/scroll-default-bottom.png, scroll/scroll-prepend-anchor.png; live/ contains desktop/mobile light/dark and cold-restart screenshots. JSON records include isolated profile identity, random ports, fakeCLI path, process cleanup and no-interception claims. Final live ports 49667 and 54986 were released; live remainingOwnedProcesses is empty.

Runtime tests ran against the working tree before its final commit, so live/evidence.json sourceSha records the baseline HEAD, not a claim that baseline code passed. Source changes were staged during verification. Final commit includes those source files and this evidence.

Not exercised: real provider CLI or user production data, remote CI, manual human acceptance, delayed media layout changes above a non-tail viewport, assistive-technology interaction. Same-user loopback assertions retain the existing local trust model and are not remote authentication. Server cursor optimization is outside this minimal increment.

No main/practical movement, push, release, deployment or protected service restart. Candidate requires MA acceptance before fast-forward integration.

Final commit hook caught two unsupported Testing Library exact options in the newly added regression. Replaced them with anchored name regex; product code unchanged. Affected frontend suites rerun and commit TypeScript hook required to pass.
