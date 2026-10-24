# React Console — real Vite + React panel

A minimal plugin UI built by Vite with React and served from a versioned,
immutable panel build URL.

```text
index.html, src/     React source
build.mjs            Vite build (relative base, JSX via esbuild)
manifest.json        web-app/v1 declaration
dist/                generated (gitignored)
```

## Dependencies

`build.mjs` first looks for this plugin's own `node_modules` (`pnpm install`), then falls back
to `packages/web/node_modules` in the Pan checkout. The acceptance test links the checkout's
installed dependencies through a directory junction, so it builds offline without downloading
or changing shared packages.

## Try it

1. Add `examples/plugin-react-panel/manifest.json` to `plugin_manifests`.
2. Settings → Plugins → Local plugin panels → **Build**, then **Enable**.
3. Open **React Console**. `Call /api/health` uses the ordinary Pan API without a bearer token.
4. `Load lazy chunk` requests the dynamic `lazy-*.js` chunk only on click. Rebuild after editing
   `src/lazy.jsx`; an already-open page keeps loading its old immutable chunk, while a newly opened
   page uses the new build.

`base: './'` is required: the entry HTML and lazy chunks must resolve under
`/panel-assets/<plugin>/<panel>/<buildId>/`.
