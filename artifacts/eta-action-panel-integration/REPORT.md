# ETA / cached Action Panel / Session query integration candidate

TA handoff; independent MA acceptance and overall human acceptance remain pending.
Worktree `D:/project/pan-worktrees/event-trigger-ui-20261022`, branch
`feature/event-trigger-ui-20261022`.

## Inputs and merge decisions

The tree started clean at `e57bda29713f991530fe3448df24e6fb167571f3`.
Live refs at task entry were:

- accepted ETA integration `e5e9f05ca0c3e098b55eb2dc346f2b1df6b62f7d`;
- requested main `fd15054c3fe78cf1beb419ec4c3f0ab35b969b28`.

ETA integration was a fast-forward. Main was merged without moving its ref,
as `7fad95eb5504d35be28679e1cc552f4cc854f6e8`. Only `ActionsView.tsx`
had a text conflict. Resolution retained main's **new compact list/full-panel
editor/Duplicate/cache implementation** and removed the old monolithic editor.
The small ETA compatibility logic was ported into `ActionGroupEditors`,
`actionsModel` and typed selectors. The shared `actionRegistryStore.ts` itself
is byte-identical to the requested main version; ownership/dedupe/TTL/late-GET
fencing and immutable Group/pin behavior were not replaced.

MA then expanded this task to accepted query backend `1edad837fcab3cba345864fcba12c47b150c62a0`
(implementation `4dd87d660c594267d63275ea15a1e78f85a41790`). It was merged
as `db34b9aaa607a2f7e56c028dfecf9841f025e701`. Conflicts were confined to
`Event-Trigger-Action-v0.1-{implementation,core-contract}.md`: retain both the
accepted external plugin lifecycle supplement and the newly authorized query
contract; replace the obsolete query-reserved paragraph. No backend conflict
was resolved by rewriting implementation.

Source comparisons confirm all `packages/actions/*`, automation engine/store
are identical to accepted query input, and all `packages/plugins/*` are identical
to accepted ETA integration. No new business backend/engine/plugin implementation
was authored here. All three input commits are ancestors of this candidate.

Other agents subsequently advanced main/practical to `4ae71124` and the ETA
integration ref to `301af488`. These are observations only: this task did not
move those refs or silently chase their newer heads. The explicitly inspected
main input remains fd15054c.

## Frontend result

- `components/actions/ActionGroupEditors.tsx`, `SessionOperationFields.tsx`,
  `actionsModel.ts`, `types/actions.ts`: Session Operation is a real execution
  kind in the new full-panel editor. Edit explicit targets (shared lazy Session
  cache, missing-ID retention, manual refresh), Event paths or Session query.
  Write-local is the required effect. Duplicate preserves the operation and
  creates an independent server ID; existing Group references/releases stay
  intact. Automation-only build gate rejects Session Operation in Groups,
  including rebuild; legacy API/command releases/pins retain their behavior.
- `stores/automationStore.ts`, `AutomationView.tsx`: Automation uses the existing
  shared registry read/mutation owner. Fresh route reentry does not add a GET.
  Template creation adopts its mutation response directly, with no registry or
  automation-ledger refetch. Ledger mutations do not force unrelated catalog
  invalidation. Run refresh does not pretend to refresh Action definitions.
  Live registry subscription supplies the current Action to Trigger review.
  Existing disabled drafts, memory auth, 401/409, late-response and leave guards
  remain in place.
- `stores/sessionOperationTemplateStore.ts`: lazy, deduplicated discovery through
  `GET /api/actions/session-operation-template`. Server item, field/schema and
  limits are exposed. No enumeration or execution is performed by discovery.
- `SessionQueryEditor.tsx`, `sessionQueryModel.ts`: seven fields with structured
  AND/OR/NOT groups and per-field eq/ne/in/contains. Strings, nullable manager,
  booleans and typed operand lists use appropriate controls. Name contains is
  case-sensitive literal substring; workspace contains is exact element.
  Workspace equality is an ordered array. No script/regex/SQL or arbitrary field.
  Validation mirrors 8/64 depth/nodes, 128 operands, 512 Unicode code points;
  unknown keys/types/operators and empty groups/in lists are rejected. Advanced
  JSON is optional, explained and validated; malformed raw text survives refresh
  and blocks save, with no fallback to an older valid query.
- `PauseTemplate.tsx`: consumes the **server's disabled template**, allows query,
  explicit/Event editing, and always creates a disabled ordinary Action. It never
  enables an Action or executes Pause. The user must explicitly enable the Action
  in Actions and review/enable its Trigger. Creation uses the shared registry
  mutation revision, and repeated submit is gated.
- `RunList.tsx`, `types/automation.ts`: selection mode/time/candidate/matched
  diagnostics and frozen target IDs are visible alongside the actual Action,
  arguments, per-target outcome and failed/uncertain state. Query text explains
  zero-match success, one-time execution-start freeze, no recomputation after
  rename/pin/new Session, selection not authorization and whole-selection failure
  on candidate/result overflow (2048/256), without truncation.
- Small responsive constraints in `ActionsUi.tsx` and editor grids make the new
  third execution option and nested query groups fit 320/390px. Execution options
  wrap; inputs and mobile grid columns can shrink. No whole-page redesign.

## Evidence produced in this task

Commands run in `packages/web`; no full frontend/backend suite was run.

```powershell
pnpm exec vitest run src/views/ActionsView.test.tsx src/stores/actionRegistryStore.test.ts src/views/AutomationView.test.tsx src/components/automation/fields.test.tsx src/components/actions/sessionQueryModel.test.ts --reporter=default --reporter=junit --outputFile.junit=../test-results/eta-action-panel/query.xml
pnpm exec tsc -b
pnpm exec eslint <changed TS/TSX and e2e files listed in the final lint log>
pnpm build
node e2e/eta-action-panel.e2e.mjs
node e2e/automation-review.e2e.mjs
```

- **79 passed, 0 skipped**, exit 0 (`vitest.xml`, `vitest.log`): 16 Action Panel,
  16 cache, 14 Automation, 5 literal-field and 28 query-validation cases.
- Typecheck/build exit 0; **80** precompressed production assets verified.
  `build.log` retains the existing router/chunk warnings.
- Directed lint exit 0; one pre-existing Fast Refresh warning in the now-touched
  shared `ActionsUi.tsx`, no suppression (`lint.log`).
- Real production Chromium and isolated FastAPI: **10 coexistence/query scenarios**
  pass (`browser-results.json`, `http-evidence.json`, `browser.log`). These include
  first registry GET=1, fresh Action→Automation→Action GET=0 additional, no eager
  MCP catalog, edit and duplicate/save response adoption with no extra registry
  GET, ordinary template mutation with no ledger GET, real Group build 400 gate,
  unchanged legacy immutable release, delayed pre-save GET fencing, manual
  refresh convergence, missing-ID editing, query template disabled, raw error,
  nested controls, real fake-CLI running→idle Event→query→Pause→Run diagnostics,
  rename after freeze, zero-match success, server invalid-type 400 with unchanged
  registry, query duplication and independent disabled copy. Browser page errors=0.
- Separate real-browser repair regression: **5 passed**, exit 0 (`guards-results.json`,
  `guards.log`), covering raw literal/POST equality, dirty cancel/accept, 409
  rebase/server-load, invalid input preservation and pending duplicate-submit gate.
- `query-{320,390}.png`, `session-operation-{320,390}.png`, `query-run.png`:
  inspected production screenshots. Final rerun also captures query bottom controls.

Post-commit exact-SHA verification is written to the gitignored directory
`packages/web/test-results/eta-action-panel-final/`; its `browser-results.json`
records the precise tested SHA reported in the final handoff. Versioned evidence
here describes development validation before that final commit.

## Independent input evidence (not this TA's new test counts)

- `audit/action-panel-ma-20261022/README.md`: MA accepted source `7c00d4ff`,
  independently ran 43 frontend and 84 backend tests and a real isolated browser
  cache probe. This is the source of truth for the main Panel's earlier acceptance.
- `artifacts/event-trigger-ma/UI-ACCEPTANCE.md`: accepted ETA UI source e57bda29,
  independent 32-test rerun and reviewed TA browser repair evidence, without
  claiming a new MA browser run.
- `artifacts/event-plugin-rework/REPORT.md` plus integration merge e5e9f05c:
  accepted per-plugin lifecycle fencing, no global external-handshake lock;
  retained unchanged here. This task does not re-claim external transport tests.
- `artifacts/session-query/REPORT.md`: query implementation 4dd87d66 and prior
  backend/HTTP evidence. MA's task message additionally reports an independent
  55-test query/concurrency acceptance; this TA did not repeat that backend suite.

## Isolation, failed experiments and limits

Owned dynamic ports only, never 8767/8768. Every HTTP/browser management write
checks checkout, runtime, Session path, PID/Windows venv parent identity and fake
CLI. Cleanup retires only that process tree and proves port release. Checkout
auxiliary data hashes are unchanged. The server's exit 1 is deliberate owned
taskkill, separate from browser exit 0. No provider/model/account invocation,
shared dependency writes or existing service/user-data operations occurred.
The harness omits login/logout bodies and all Authorization headers from evidence.

`before-typecheck.log` proves the mechanical merge alone did not support the new
execution union: the new command editor tried to read executable/args on Session
Operation. Preserved failed browser attempts include an obsolete old Actions
heading assertion (`guards-initial-failure.log`) and an external-client registry
write followed by a stale cached duplicate (`browser-external-revision.log`).
The latter correctly rejects a stale revision; the probe now explicitly Refreshes
after its own external writes, as required by snapshot/TTL semantics. Per-attempt
runtime/server logs remain in `packages/web/test-results/eta-action-panel-*`.
A narrow-screen attempt revealed inner-container clipping despite outer width
looking correct; minmax mobile columns/wrapping controls fixed it. The final
probe checks internal containers and visible bottom Save controls too.
One post-commit attempt completed query execution but timed out while clearing
animated toast siblings for a screenshot. The harness now invokes the existing
dismiss handlers together for screenshot cleanup, avoiding indexed locators
changing identity as siblings retire; product workflow clicks are unchanged.

Not newly tested: actual paid provider, full external-plugin transport/lifecycle,
Windows native beforeunload dialog interaction, large-ledger performance, query
candidate/result overflow using thousands of real Sessions, real crash recovery,
or integrated human acceptance. Query limits/types are validated in unit cases;
runtime limit enforcement relies on the accepted backend evidence. No new backend
freeze race or permission guarantee is claimed. Group release immutability is
checked through real HTTP; live Worker release pin transitions were not repeated.

No main/practical movement by this task, no push/release/deployment, no child
delegation. Stop after the clean candidate and wait for MA acceptance.
