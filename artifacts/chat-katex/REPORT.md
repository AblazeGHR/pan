# Chat KaTeX candidate evidence

- Branch: `fix/chat-katex-repair-20261028`
- Worktree: `D:/project/pan-worktrees/chat-katex-repair-20261028`
- Baseline: `511784fe43107a3a290236841ddb9b618c1cf709`
- Source/harness/test commit: `b81c8a9ef958e193866b268fc2dfd2808377c1e3`
- The following evidence-only commit contains this report and logs/screenshots. The final candidate SHA is its Git HEAD (reported in the delivery message).

## Change

Installed `remark-math ^6.0.0` (resolved 6.0.0) into the existing React Markdown pipeline's stable plugin array. Imported `katex/dist/katex.min.css` in MarkdownRenderer and added one local display-math overflow rule. Existing rehype-katex settings and URL/HTML safety behavior are unchanged: no trust, raw HTML rendering, body rewriting or custom parser was added. No API/schema/backend changes.

Runtime files: `packages/web/package.json`, `pnpm-lock.yaml`, `src/components/chat/MarkdownRenderer.tsx`, and `src/components/chat/markdownMath.css`. Dedicated tests and the owned production harness live alongside these changes. No ChatMessages, Remote settings, history, import protocol, shortcuts, global theme or service files changed.

Verified syntax: `$x^2$` inline, standalone `$$` blocks, escaped currency dollars and lone unmatched dollars. Paired `$5$` uses remark-math's formula syntax; no currency heuristic was introduced. An open inline delimiter stays literal until completion. A flow-math block can extend to EOF, following remark-math syntax; invalid partial TeX renders an error within the message and reparses on completion.

Existing fence behavior was confirmed before repair and retained: `math` fences render through rehype-katex; `latex`, ordinary and JS fences stay code. Dollar signs in inline code stay code.

## Reproduction and verification

Commands below run in `packages/web` unless indicated. Short commands completed within three minutes; no persistent background Job was required.

| Check | Result | Evidence |
| --- | --- | --- |
| Baseline new dedicated test file, before dependency/renderer changes: `pnpm --dir packages/web exec vitest run src/components/chat/MarkdownRenderer.katex.test.tsx` (repository cwd) | 7 failed, 3 passed; inline/block render assertions found no KaTeX. The PowerShell wrapper returned 0 because it ended with Get-Content; the failing test subprocess numeric exit was not separately saved. | [red.txt](red.txt) |
| `pnpm exec vitest run src/components/chat/MarkdownRenderer.katex.test.tsx src/components/chat/MarkdownRenderer.test.tsx src/components/chat/searchText.test.tsx src/utils/attachmentMarkdown.test.ts --reporter=default --reporter=junit --outputFile=../../artifacts/chat-katex/focused.xml` | Exit 0; 4 files, **52 passed**, 0 failed, 0 skipped. JUnit was moved from Vitest's src-relative output location into this evidence directory. | [focused.txt](focused.txt), [focused.xml](focused.xml) |
| `pnpm exec tsc -b --noEmit` | Exit 0, no diagnostics | [tsc.txt](tsc.txt) (empty success output) |
| `pnpm build` | Exit 0; Vite build succeeded; 90 compressed production assets verified. Vite warned about chunks over 500 kB. | [build.txt](build.txt) |
| Normal `git commit` with `core.hooksPath=scripts` | Exit 0; normal pre-commit ran `npx tsc -b --noEmit`: React frontend OK | [commit-hook.txt](commit-hook.txt) |
| `node e2e/chat-katex/production.mjs` after `pnpm build` | Exit 0; fresh production harness compiled using repository Vite config, real Chromium **153.0.8010.12**, owned random HTTP port **63860**, no Pan/provider calls. | [browser.json](browser.json), [browser.txt](browser.txt) |
| `git diff --check` | Exit 0 | Delivery check |

The focused matrix covers inline/block rendering, real MathML and visual HTML, plain/JS/latex code protection and existing math fences, invalid TeX containment, incomplete inline/block and streamed completion, escaped/lone/paired currency delimiters, Markdown/GFM/table/link/attachment preservation, ordinary search marks, and inert untrusted KaTeX HTML/href commands/raw script HTML. The untrusted HTML-extension test emits the expected strict-mode warning; no warning was suppressed.

Evidence formatting: JUnit hostname was anonymized as `owned-isolated`. Raw `build.txt` and `red.txt` contained tool-generated trailing spaces, which made the first staged evidence diff check fail. Their byte-exact originals are retained in `build.txt.gz` and `red.txt.gz`; the readable text copies only normalize line endings/trailing whitespace. No assertions, error messages or outcomes were removed. The final complete candidate diff check passed.

## Real Chromium evidence

- The component harness imports the actual MarkdownRenderer and application CSS. It uses a fresh production build, a new browser context and owned HTTP listener, without Pan data/profile/provider/backend. It aborts and fails unexpected API/WS/external requests.
- Actual rendered `.katex`, `.katex-display`, MathML and annotation nodes were asserted, including completed streaming and invalid-formula containment. Code, links, attachment markup, GFM table and active ordinary-text search marks were checked.
- The normal application's fresh `dist/assets` CSS references **59** KaTeX font files; every referenced file exists and is nonempty. In Chromium, production CSS plus Main/Math/Size2 WOFF2 resources returned **200**. `document.fonts` reports the corresponding KaTeX fonts loaded; computed math font is `KaTeX_Math`, and MathML has the expected visually hidden positioning.
- Desktop **1440x1100**: document width 1440, formula container width 780. Narrow **390x1100**: document width 390, formula container width 350; the deliberately wide equation has scrollWidth 832 and actually scrolls within its own block. No horizontal document overflow.
- **0 pageErrors, 0 request failures.** Screenshots visually reviewed: [desktop.png](desktop.png), [narrow.png](narrow.png).
- The first harness run omitted the application's body background class, producing low contrast on white despite structurally passing. This was a harness fixture defect, corrected before final visual review. Initial evidence is retained as [initial-harness-narrow.png](initial-harness-narrow.png) and [initial-harness-browser.json](initial-harness-browser.json); these are not final visual acceptance.
- Harness `finally` closes its browser and HTTP listener; the final command exited 0. A post-run listener query found no listener on the owned final port 63860.

Re-run: install with `pnpm install --frozen-lockfile`, run the focused tests/tsc/build above, then `node e2e/chat-katex/production.mjs`. The harness builds into ignored `packages/web/test-results/chat-katex-production`, uses port 0, and writes evidence here. It does not edit production dist.

## Delivery boundaries

`main` and `practical` remained `511784fe43107a3a290236841ddb9b618c1cf709`. No push, target-branch move, deployment, provider request, actual user data/profile access or restart of an existing service occurred. Owned test processes/listeners were closed. The final candidate includes source and evidence, with a clean working tree checked at delivery.

This proves local source, focused unit tests, TypeScript, normal hook, production build/font assets, and actual isolated Chromium rendering of the production MarkdownRenderer. It does not establish remote CI, three-platform execution, full application/backend HTTP integration, real provider streaming, real attachment download, user-data migration, deployment, or human/MA acceptance. Those layers were not exercised. MA independently accepts and advances protected refs under the requested fast-forward strategy.
