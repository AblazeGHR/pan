# Composer URL node — targeted validation (A/B/C/D)

Run date: 2026-10-18
Branch: `feature/composer-url-node-20261017`
Base / current `main`: `e51b4ba50e22db464b8d45c5f3f1f2d4d99b5d4c`
A–D implementation: `650a0afaffcd8d91df1cf02d53b826596c4a7e55`
Evidence refreshes: `ac22e136`, `7d834fe1`, and this documentation commit.
No reset / no cherry-pick; `main` is an ancestor (`git merge-base --is-ancestor main HEAD` = 0).
`19ec5f73` is an ancestor, not the tip.

`19ec5f73` is an earlier evidence commit. The `main` merge and the A–D work are
in `f5d1c447` (merge), `650a0afa` (identity + HTTP + mobile + copy/undo), and
the later evidence commits. The tip is the commit reported in the final chat
message; `19ec5f73` is not the tip.

## A. Real mouse URL reorder (not dispatchEvent)

Command (owned frozen-install tree, same `src` as HEAD):

```text
node e2e/composerUrlNode.e2e.mjs
```

Method: Playwright `locator.dragTo()` — real mouse press/move/release, not a
constructed `DragEvent`/`DataTransfer`.

Result from `evidence.json` (port `57285`):

```json
{
  "pasteId": "url_c0dbd9b4-1b21-4ea8-994a-64e67ad4fa68",
  "dragId": "url_c0dbd9b4-1b21-4ea8-994a-64e67ad4fa68",
  "dragOrder": ["text", "url"],
  "dragCount": 1,
  "errors": []
}
```

The node moved to the end, exactly one node remained, and the local occurrence
id survived the move. A copy/paste still creates a new id (unit test).

## B. 390px mobile: long URL, delete, send, Session draft switch

Same Chromium run, viewport `390x780`:

```json
{
  "node":   {"x": 27, "y": 703, "width": 341, "height": 22},
  "editor": {"x": 12, "y": 694, "width": 371, "height": 70},
  "delete": {"x": 345, "y": 706, "width": 16, "height": 16}
}
```

- The long URL node stays inside the editor on both sides; the delete button is
  inside the editor and was clicked successfully.
- The long URL was sent; queue text is
  `[<long-url>](<long-url>)`.
- Session switch: `s2` composer has 0 URL nodes; `s2` was given its own
  `"s2 draft"` text (PUT persisted) and restores that text when revisited;
  back on `s1` the same node id
  (`url_ef4b27e3-81ae-4caf-bbe8-3a107489060f`) is restored.
- A real page reload restores the same id and URL from the draft PUT/GET path.
- Real system clipboard round-trip (A3): `Control+C` on the node wrote
  `[Clip Docs](https://clip.test/a?q=1#h)` to `navigator.clipboard`; after
  clearing the composer, `Control+V` recreated a `Clip Docs` node at
  `https://clip.test/a?q=1#h`.

Boundary: the App-level mobile drawer is not mounted in the ChatView-only
fixture. The switch calls `useSessionStore.getState().selectSession`, the single
production entry point every session card uses. Layout, click, and draft
restore are real Chromium.

Screenshots: `01-paste-node.png`, `02-mouse-drag-node.png`,
`03-delete-node.png`, `04-mobile-long-node.png`,
`05-mobile-draft-restored.png`, `06-mobile-sent.png`,
`07-clipboard-roundtrip.png`.

## C. Real Pan HTTP: draft URL part + Markdown message/parts

Command (owned full-repo copy, dynamic loopback port, E: miniforge Python):

```text
python packages/web/e2e/composerUrlNodeHttp.e2e.py
```

Real FastAPI routes + uvicorn + the existing deterministic fake CLI
(`tests/support/frontend_stream_cli.py`, no model/network). Port `60474`,
runtime under the copy's `test-results/composer-url-node-http/runtime`.

Results in `http-evidence.json`:

- Draft A PUT/GET returns `{type:"url", id:"url_http_a", url:"...?q=1&x=2#h",
  label:"Docs"}`; Draft B returns a different URL. Neither leaks into the other.
- The sidecar file on disk contains the URL part.
- `POST /api/sessions/{id}/queue` with the composer's `text` + `parts` is
  persisted and read back with the same Markdown. The worker/provider boundary
  returns `answer:See [Docs](https://example.test/send?q=1&x=2#h)`.
- `POST /api/send` with `Legacy [Docs](https://example.test/legacy?q=1#h)`
  reaches the provider and returns `answer:Legacy [Docs](...)`.

This is the real HTTP path, not the Playwright `/api/**` mock.

## D. Duplicate intent, Markdown copy-back, Undo

- Repeated URL: two pastes of `https://dup.test/a` produce two distinct ids and
  the outbound payload has two links (no URL-based dedupe):
  `[https://dup.test/a](https://dup.test/a)[https://dup.test/a](https://dup.test/a)`.
- Copy-back: copying a node writes the Pan MIME plus `text/plain` Markdown;
  `parseComposerText` recognizes standard `[label](url)` Markdown and recreates
  a labeled node even when only the plain-text flavor survives.
  Unsafe Markdown destinations remain text.
- Undo audit (real Chromium): Ctrl+Z after a URL node paste and after an
  attachment node paste are both no-ops (`before/after` counts both 1). This is
  the existing atomic-node transaction behavior; it is documented in
  `commitReplacement` and no regression was introduced.

Targeted D tests (name filter `URL`):

```text
36 passed | 90 skipped (composerUrl / RichTextComposer / InputRow)
```

Backend draft URL-part schema: `9 passed`.

## Combo build + card regression

```text
pnpm build   -> tsc -b + vite build; Verified 66 compressed production assets
session-card-height e2e -> EXIT 0, ok: true (desktop/mobile), port 59937
composer URL e2e        -> EXIT 0, errors: [], port 57285
```

## Environment / ownership

- Browser harness: Vite + real Chromium, dynamic port; `/api/**` mocked at the
  Playwright route boundary (explicitly noted).
- HTTP harness: real uvicorn/FastAPI in an owned full-repo copy; all data under
  that copy's test-results runtime; no worktree `data/` write-through.
- Build ran in `D:/project/pan-worktrees/composer-url-node-20261017-webverify`
  with frozen install because the shared junction lacks `@xterm/*`.
- Never used 8765/8767/8768; no main/practical/push/restart changes.
