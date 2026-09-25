# Pan browser trace tools

These scripts observe a Pan page in a dedicated Chrome or Edge window. They record WebSocket frames, the frontend message store, and rendered DOM rows so message duplication or ordering issues can be compared across layers.

The recorder does not start, stop, or restart Pan. It launches a separate browser profile and stores trace files under `packages/web/test-results/`, which is ignored by Git.

## Quick start

Requirements: Windows, Node.js 24 or newer, and Google Chrome. Edge can be used by passing its executable path as described below.

1. Make sure Pan is already running and open at the address you want to observe.
2. Double-click [Start-Pan-Browser-Trace.cmd](Start-Pan-Browser-Trace.cmd). A dedicated Chrome window opens on Pan. The launcher adds `panE2E=1` to the address so the read-only frontend store inspection hook is available.
3. Reproduce the message issue in that dedicated window. When it appears, return to the command window and press Enter. This writes a timestamp marker and screenshot into the trace.
4. When finished, type `q` and press Enter. The recorder closes the Chrome process it started and prints the trace directory.
5. Share the printed trace directory when asking for analysis. The `trace.ndjson` event stream and marker screenshots are inside it.

The trace directory is named `packages/web/test-results/browser-trace-<timestamp>/`. It contains `trace.ndjson`, marker screenshots, `summary.json`, and the dedicated `chrome-profile/`. The profile can contain browsing data created in that browser window; remove it manually when no longer needed.

## Use another Pan URL or browser

From the repository root, run the PowerShell launcher and pass the Pan URL:

```powershell
& '.\packages\devTools\Start-Pan-Browser-Trace.ps1' -PanUrl 'http://127.0.0.1:8768/react/'
```

For Edge, pass the path to `msedge.exe`:

```powershell
& '.\packages\devTools\Start-Pan-Browser-Trace.ps1' `
  -PanUrl 'http://127.0.0.1:8768/react/' `
  -ChromePath 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
```

If Edge is installed elsewhere, substitute its actual executable path. The option remains named `ChromePath` for compatibility with the recorder script.

By default, WebSocket payloads are summarized with event identity, sequence, text length, and hashes; message bodies are not saved in the trace. Screenshots still show visible page content. Add `-FullContent` when exact payload text is needed; those payloads are written only to the local trace directory.

## Inspect the open browser

While the dedicated browser is running, run this from the repository root:

```powershell
node .\packages\devTools\inspect-browser.mjs
```

The command prints the active page URL, selected Session, recent store identities, and rendered row indexes. Add `--screenshot` to save a current screenshot next to the trace:

```powershell
node .\packages\devTools\inspect-browser.mjs --screenshot
```

The launcher tracks the first tab it opens. Reproduce the issue in that tab. To follow another page, restart the trace with its URL passed to `-PanUrl`.

## Reading a trace

- `ws_received` records decoded event metadata and the original WebSocket frame hash.
- `store` records before/after message identities and order. By default, message text is represented by length and hash.
- `dom` records the currently rendered virtual-list rows and their indexes.
- `marker` records when Enter was pressed and names the screenshot captured at that moment.
- `attached` indicates that the page's read-only frontend store hook was available. If it is absent, WebSocket and DOM capture can still work, but store state is unavailable.

Capture starts when the browser opens; it cannot recover earlier frames. DOM capture covers only rows currently rendered by the virtual list, while store capture includes its bounded message tail and original indexes.
