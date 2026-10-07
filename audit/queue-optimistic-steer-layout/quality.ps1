$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $root
& D:/project/Pan-main/.venv/Scripts/python.exe -c "import pathlib; paths=['packages/core/worker.py','packages/core/session.py','packages/web/server.py','packages/core/adapters/codex/app_server_wrapper.py','packages/core/adapters/codex/adapter.py','packages/core/adapters/claude/adapter.py']; [compile(pathlib.Path(p).read_text(encoding='utf-8'),p,'exec') for p in paths]; print('Python syntax OK')"
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Set-Location packages/web
& node node_modules/typescript/bin/tsc -b
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& node node_modules/eslint/bin/eslint.js src/components/chat/InputRow.tsx src/components/chat/SendQueuePanel.tsx src/services/api.ts src/stores/queueStore.ts src/types/index.ts
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& node node_modules/vite/bin/vite.js build --config ../../audit/queue-optimistic-steer-layout/vite.config.mjs --configLoader native
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& node e2e/verify-precompressed-assets.mjs
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Set-Location $root
& git diff --check
exit $LASTEXITCODE
