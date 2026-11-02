# Raw chat and failed file preview candidate

Base: 86e421555f366133b25a4069e8575c0a1310ea60
Branch: fix/editor-raw-download-20261102

## Behavior and API

History responses add rawContent from the stored body before legacy attachment normalization/editor-link projection. Live projected events carry rawEvent and terminal payloads rawResult; the existing extractor and transcript merger preserve the paired body. Raw rendering uses that body as a React text node. No per-row HTTP requests, reverse URL mapping, new persisted identities or history rewrite. Rendered Markdown/editor links continue using content.

Only existing preview failures File too large / Cannot read binary file open a read-only tab. No empty editable contents are created; store dirty/save paths and UI save are fenced. Close/delete/root reset clear previewErrors; rename moves it. Existing latest-open, Session/root and path invalidation guards reject stale reads. UTF-8 NUL data follows the existing binary preview failure. The unchanged download=1 endpoint streams original bytes with existing authorization and limits.

## Validation

- red-backend.txt: original missing rawContent assertion failed.
- red-editor.txt: both original unpreviewable-file open assertions failed.
- focused-final.txt: 196 passed across editorStore, EditorPane, EditorFileTopBar, useWebSocket and MessageBubble bookmark tests; exit 0.
- backend-final.txt: 68 passed across attachment protocol, runtime diagnostics and Session summary projection; exit 0. Two upstream websockets deprecation warnings.
- build-final.txt: pnpm build exit 0, TypeScript + Vite production build + 90 compressed assets verified. Existing chunk-size warning.
- chat-tests.txt: 141 passed, one E1b turnId exact-shape assertion failed. baseline-E1b.txt reproduces the same extra turnId with HEAD useWebSocket.ts; original assertion retained. The baseline log also retains an unrelated trailing pnpm resolution error from the command wrapper. No skip/weakening added.
- browser-complete.txt: fresh owned production FastAPI + dist + headless Chromium, dynamic port 55811, disposable runtime, exit 0 and owned listener cleanup. Exact history raw body (fences, literal HTML, local and web links), live and terminal raw, desktop 1440/mobile 390 large and binary readonly Editor navigation, download bytes equal, save disabled, foreign-Session negative control, held real file response across Session switch rejected. Existing fixture injection only seeds provider/history input; real product HTTP/WS and UI are exercised.
- browser-first/second/third.txt preserve fixture navigation/selection timing failures. browser-final.txt is the earlier passing browser run. Current reproducible runner: node packages/web/e2e/editor-raw-download-runner.mjs.

Dependencies installed only inside this tree using pnpm install --frozen-lockfile. Background Job connector attempts returned reauthentication required for both available links; no Job was created, no manual notify or polling used. Validation ran as short isolated local commands.

## Limits and handoff

This is candidate validation, not MA acceptance, remote CI, deployment, physical mobile-device or real provider validation. Windows local only; Linux/macOS and full suite not run. Normal hook is required on commit. MA owns independent acceptance and practical integration. No push, protected-branch update, real profile/data or real service operation was performed.
