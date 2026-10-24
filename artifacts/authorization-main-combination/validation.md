# Authorization + main Locker combination

Tested source commit: `bf81b916199ca1a10b523fa6a0c379daef31ec42`.
This is a real merge, preserving both parents:

- Authorization candidate `0460bf49c667545ecc166addc1eca6ea049892b7`.
- Requested main `8f44cd734646d8ece2030ba85501c73e4eefc13b`.

The final delivery adds evidence only after that source commit. `results.json`
records exact Git object IDs for the `packages`, `tests` trees and root manifest;
these must equal the final candidate's same paths. The live HTTP evidence records
the tested source commit directly. `build-sha256.json` identifies the generated
production output used by Chromium. No rebase, push, or main/practical update.

## Merge resolution and source changes

Only HTTP reference documentation had a textual conflict. Both the four-object
Authorization and ordinary Locker producer sections were retained. MCP server
merged automatically and keeps both registered tools: `authorization_objects`
and `locker_message`. The core manifest retains Locker instructions and now
describes the separate four-object matrix explicitly.

Locker keeps its existing source-bound local stdio transport, user Bearer reads,
manual read semantics and trusted choice barriers. It does not enter the
four-object path allowlist or acquire a new Agent matrix gate. No Locker product
code was changed to accommodate the merge.

The existing Locker live fixture's Action creation and Automation reads now pass
its owned user's Bearer, as its Trigger creation already did. The existing
production assertions remain. Additional assertions establish both MCP tools
are discoverable in real stdio and all 22 Agent grants remain false at revision
0 before and after message creation, manual reads, choice execution and cold
restart. The fixture's output is directed to this combination evidence directory
instead of overwriting main's historical audit artifacts.

## Narrow validation

All commands exited 0, with no failures or skips and no relaxed assertions.
The previous 456-case Authorization evidence remains in
`artifacts/authorization-candidate`; it was not rerun or relabelled as a run on
this merge. Only the following affected selections were run on the merge:

```text
python -m pytest -q tests/test_authorization_mcp.py
  tests/test_locker_message_producer.py tests/test_locker_messages.py
  tests/test_user_locker_lifecycle.py tests/test_locker_trusted_binding.py
  --junitxml=artifacts/authorization-main-combination/combination-unit.xml
=> 66 passed, 0 failures/errors/skips, 9.596s

npx vitest run src/components/layout/AuthorizationSettings.test.tsx
  src/views/LockerView.test.tsx src/views/lockerModel.auth.test.ts
=> 3 files / 23 passed, 0 failed/skipped, 3.83s (cwd packages/web)

npm run build
=> tsc -b + Vite + precompressed verification passed (cwd packages/web)

python -m pytest -q tests/test_locker_product_live.py
  --junitxml=artifacts/authorization-main-combination/combination-live.xml
=> 1 passed, 0 failures/errors/skips, 30.030s
```

The live test used a temporary profile and random port **56623**, production
Chromium with no API interception, real UserAuth, and real stdio discovery /
create / lookup / idempotent replay / conflict / foreign-source rejection. It
also exercised protected attachment downloads, Workspace filtering, manual read
and unread, source Read all, choice-triggered Pause, no-choice reply, rejection,
terminal deletion, deleted-source history, expired login and cold restart with
no repeated notifications. Screenshots cover 1440/768/390/320 light/dark. Existing
source/target/choice safety tests retain their business assertions. Default grants
are unchanged, the historical ordinary record survives restart, and the trusted
user-authorized choice succeeds with Agent grants off.

Cleanup evidence: server exited, random port released, zero remaining owned
processes. No production service or port 8767/8768 was accessed. No persistent Pan
Job or sub-agent was created. No full-suite run was started.

## Why 92 rather than 94 compressed assets

The verifier counts **two compressed files** (br + gz) for each JS/CSS chunk at
least 1024 bytes. It does not count application entries or feature completeness.

The original Locker worktree is still at the exact requested main SHA; its
existing build has 62 plain JS/CSS chunks and 94 verified compressed files. The
combined build has 61 plain chunks and 92 compressed files. The one disappeared
standalone chunk is `actionRegistryStore-2pUi6I2N.js` (6120 bytes), accounting for
exactly two compression checks. Sourcemaps prove `src/stores/actionRegistryStore.ts`
is now inside `automationStore-CA8MJGyZ.js`, rather than a separate chunk, due to
the Authorization store's shared import. It has not been removed.

Locker remains independently emitted as `LockerView-XWzMDmtp.js` (23401 bytes)
and `LockerView-LR5SH6UQ.css` (9786 bytes), each with matching br/gz. The JS map
contains both `LockerView.tsx` and `LockerMessageRow.tsx`. Actual production route
loading and all live interactions above succeed. The asset list and source-map
mapping are recorded in `assets-comparison.json` and `asset-source-map.json`.
The older main checkout's unrelated/stale dist was not used as the 94-asset
comparison; the comparison uses the Locker worktree at 8f44cd73.

Known non-failing warnings remain: unavailable pytest timeout plugin, Starlette
TestClient deprecation / Pydantic forward-reference warning, and existing Vite
router dynamic-import / large-chunk advisories. This is a locally verified clean
fast-forward candidate for MA review; no integration or acceptance is claimed.
