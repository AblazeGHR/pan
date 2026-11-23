# Practical/main CI integration 2026-11-23

Task PRACTICAL-MAIN-CI-20261123-1. Base main `4d99756ed6d0a9616a781e52871450c83c2b270a`; source practical `b16c06f217c8341c794d590837e88c31132866bb`. Candidate branch `fix/practical-main-ci-20261123`, worktree `D:/project/pan-worktrees/practical-main-ci-20261123`.

## Source disposition (in progress)

| Original SHA | Capability | Disposition |
|---|---|---|
| e10763474b1a5a8b7e21331dc5b4455486ad4306 | Structured terminal report policy | Port config normalization, persisted API and worker report gate; preserve history/diagnostics |
| f7514d7a71cc6d5a1529d4e5e6fa2717556cfffb | Terminal Behavior settings | Port UI/store; adapt to main shared settings descriptions and stable search anchor |
| 41b44da58ccb7098ea2a035d079bb7664361cc9a | Policy persistence/failure tests | Port tests |
| 0a809d9d71d16ab2030dd6bcb4ea9033f197da74 | Production Behavior browser | Port assertions; combination verification pending |
| 47e42fdb70538b9ec13c9688e5412a03a47ded75 | Remove unrelated recovery helper | Preserve removal; no recovery admission helper introduced |
| 5a05bc3a745ec28001ae8d39e1e823d49c39715a | Schema classification suggestions | Port read-only schema API and suggestion UI/tests |
| 746187a7131c269085f08ba76be8d5256e388c20 | Codex limit observation | Port standalone script/tests only |
| 50a4ff3587a0629aaf4cb5f45bc1f26aa1199cd6 | Durable quota recovery receipts | Port standalone script/tests only; no execution or real account access |
| 1f3f525d282697e1a9a614a42a1c8aa74142421f | Continuation queue-front after accepted restart | Port standalone script/tests; do not activate |
| 1e531c2f2dcb54f6a6bab6b10a6e6a61aabb3438 | Global administrator authentication | Compare main 232b8d1b: global_auth, channel_auth, local_transport, admin, platform auth lock/plugin IPC implementations equal. Preserve main canonical automation, System Action IPC and new Automation layout; add missing verifier-not-persisted assertions |
| b16c06f217c8341c794d590837e88c31132866bb | Local exit endpoint identity | Port listener platform layer, launcher identity/readiness, diagnostic and CLI shortcut changes. Resolve CLI/server/tests around main fixed System Action and canonical receipts; never restore old writer |
| d88df38e7f96d678c43f1789c9b7192ccbff1b53 | CBC echo historical browser evidence | Evidence-only: source products already inherited; do not copy screenshots/runtime JSON with old Session identities. Historical evidence does not prove this combination |

`audit/exit-auth-20261122` files belong to b16c06f2 predecessor validation (169 cases, old legacy exit Job receipt, local host paths/hostname). Keep source archive accessible via its SHA; do not copy runtime metadata, compressed logs or predecessor results into new acceptance. Port its product/tests/platform documentation and preserve prior report with historical scope.

## Combination findings

- Preserve main CI sharding/identity gates, canonical Actions/Jobs and System Actions read-only policy. No whole practical tree merge.
- Initial real exit regression proved host exited but canonical offline proof scanned only port and saw the foreign 127.0.0.2 listener. Adapt `packages/core/platform/execution_host.py` directly to the imported address-scoped listener primitive and capture original bindHost before stop clears state. Query denial remains unknown, never success.
- Initial full Vitest: 2877 passed, 3 skipped, 1 failed at foreground activity through body parsing. Main global authentication delayed foreground admission until after dynamic auth import. Move admission to cover authentication preparation too; explicit body-entry barrier preserves parsing/failure release assertions.
- First focused backend: 142 cases, 4 failed, 0 skipped. Keep red `.tmp/practical-main-ci/focused.xml`; includes real canonical exit unknown, two old route/receipt test assumptions, and missing hashlib import. Adapt tests to actual canonical entry/receipt rather than restoring legacy writers.
- Old CI 43700210080 head is 74f475250b54959aaab13ffa83be820cada68a32, not this candidate; only historical comparison.

## Execution evidence (pending final acceptance)

- Dependency installation Job `job_0f7c03ca626e13d6e305c9a9`: exit 0, owned worktree venv and frozen pnpm install.
- Initial backend Job `job_ab7c1727491c982f1266c7c8`: exit 1, red above.
- Initial frontend Job `job_e039b893b10ff3d2260a4083`: exit 1, full Vitest red above; build correctly did not run after failed tests.
- Backend iteration Job `job_02ae97d6cf1a0412875dd29b`: exit 1, 141 passed/1 new assertion attempted sessions read before login; moved check to actual persistence, `.tmp/practical-main-ci/focused-v2.xml`.
- Frontend iteration Job `job_9aa0516988023d70c6f53d72`: exit 0, 251 files, 2878 passed/3 existing skips; production build and 98 compressed assets verified.

Original Pan Jobs provide terminal notifications; no OS watcher/polling. Both old worktrees remain dirty and untouched. No target ref changes, candidate push or CI dispatch yet. Exact final SHA and final run identities remain pending.

- Combined real HTTP/MCP/Chromium Job `job_862be3bd8895352aed5018a8`: 147 cases, 143 passed, 4 Behavior tests rejected by authentication. Source fixture needed explicit owned credentials; production middleware remains enforced.
- Final Behavior HTTP/production Chromium Job `job_f67fc123c3c293adc55677e7`: exit 0, 4 passed/0 skipped. Explicit fixture login, persisted cold reload, two-page Behavior settings, terminal classification policy and Search-to-real-control jump verified. XML `.tmp/practical-main-ci/behavior-live-v4.xml`. Core/classification/auth/lifecycle/standalone-script 143 passing cases are retained from prior Job; not a claim of a final full backend run.

Normal hooks and remote exact-SHA gate results follow in continuation evidence. No test assertion, CI shard/matrix or unknown policy was weakened.

## Scope revision 2

MA added only committed increment `caea000fb52b303bb2240a9e14303727ccdd0f68` (parent b16c06f2), restricted to `scripts/codex_limit_rotate.py` and its test file. Port stale-observation dedupe correction, read-only ledger preflight and redacted scan_complete output. No local watcher configuration/ledger copied and no real watcher started. Directed isolated Job `job_7b7ee71954c87fcacd232cc3` exit 0; XML `.tmp/practical-main-ci/codex-rev2.xml`.

Initial combination commit `b697958f0268552ca39b8ffd724379983a4c6121` is based directly on frozen main. Normal hook Job `job_a966f13de93bbdddb518bd3b` succeeded (React TypeScript check enabled). Original revisions and red/green evidence remain preserved. Remote tests workflow includes 3.12/3.14 full-collection two-shard verification gates, three platform-fast gates, authenticated Ubuntu production browser, and both Windows ownership/lifecycle combinations. Authentication workflow supplies its three-OS contract gates. Both will be dispatched on the final candidate ref and checked for exact headSha and actual job conclusions.
