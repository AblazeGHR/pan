# Local Console — buildable `web-app/v1` example

A dependency-free plugin panel that is built locally and served by Pan.

```text
src/         panel source (HTML, CSS, JS)
build.mjs    local builder: hashed assets, relative URLs, SPA entry
dist/        generated at build time (gitignored)
manifest.json
```

## Try it

1. Add this directory's manifest to `config.json` (keep existing entries):

   ```json
   "plugin_manifests": [
     "manifest.json",
     "packages/mcp/manifest.json",
     "examples/plugin-webapp/manifest.json"
   ]
   ```

2. Restart Pan or use **Settings → Plugins → Reload plugin manifests**.
3. Open **Settings → Plugins → Local plugin panels**, click **Build**, wait for **Built**, then **Enable**.
4. Open **Local Console** from the view entries. The page calls `/api/health` with the ordinary local
   Pan API boundary and reports the returned version.
5. **Disable** removes the entry but keeps the build; **Unbuild** removes the host build copy while
   leaving this source and manifest intact.

## Replacing the build

`build.command` is a plain argv list. A Vite project can use:

```json
{"command": ["pnpm", "exec", "vite", "build"], "cwd": ".", "output": "dist", "timeout_seconds": 300}
```

Set `base: './'` so generated asset URLs stay relative to `/panel-assets/<plugin>/<panel>/`.
The declared `entry` (`index.html`) is also the SPA fallback for unknown extensionless routes.

The builder here intentionally has no dependencies, so the example can be built and verified without
network access. It is not a framework template; React and Vue outputs follow the same static contract.
