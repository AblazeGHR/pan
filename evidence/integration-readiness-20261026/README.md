# QA evidence index

Product source: `43d0977f0c783f0bd69da5c4611d6bdfe8236a67`.
Baseline: `7f303faa355bfc763f0452ad68d31b920e4dc6e1`.
Branch: `qa/integration-readiness-20261025`.
Report: [INTEGRATION_READINESS_20261026.md](../../docs/reports/INTEGRATION_READINESS_20261026.md).

Committed evidence contains statistics, test identities, sanitized error classes,
commands, SHAs and entry points. Raw local logs/XML/JSON and test-produced artifacts
are retained under `.validation/qa-raw/` and are not part of the commit. No raw
profile/config/authentication material is included.

`results-summary.json` records independent invocations. Counts from repeated
invocations must not be added to the backend coverage total. The initial full
backend run timed out at the repository default 300 seconds and produced no full
JUnit. Coverage is completed by the remainder run plus every test in the excluded
concurrency file, invoked separately; the hung test has explicit timeout evidence.

## Commands

Below `PY` means `D:/project/Pan-main/.venv/Scripts/python.exe`.
Backend commands always run from the selected source tree:

```powershell
PY scripts/run_tests_isolated.py -- PY -m pytest tests/ -q --junitxml=<raw>/backend-full.xml
PY scripts/run_tests_isolated.py -- PY -m pytest tests/ --ignore=tests/test_automation_concurrency.py -q --junitxml=<raw>/backend-remainder.xml
# Each of the 8 concurrency test identities is invoked in its own process:
PY scripts/run_tests_isolated.py -- PY -m pytest tests/test_automation_concurrency.py::<test_name> -q --timeout=30 --junitxml=<raw>/concurrency-<test_name>.xml
# The 30-second diagnostic budget does not replace the preserved default-300-second timeout.
PY scripts/run_tests_isolated.py -- PY -m pytest tests/test_job_path_flip.py <all tests/test_job_final_*.py> -q --junitxml=<raw>/job-contracts.xml
PY scripts/run_tests_isolated.py -- PY -m pytest tests/test_authorization_mcp.py tests/test_authorization_password.py tests/test_authorization_permissions.py tests/test_authorization_runtime.py -q --junitxml=<raw>/authorization.xml
```

The clean baseline executes the same full `tests/` command, frontend commands,
and a targeted authority/concurrency comparison (the four authorization files
above plus `test_automation_concurrency.py`). Baseline is a fresh detached worktree
at `.validation/main-7f303faa`; tests can generate tracked audit/artifact outputs,
which are restored to HEAD after preserving raw local evidence. No product
source changes are carried into the comparison.

Frontend commands from `packages/web` in both trees:

```powershell
node node_modules/vitest/vitest.mjs run --no-cache --maxWorkers=2 --minWorkers=1 --reporter=default --reporter=json --outputFile=<absolute-raw>/vitest.json
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
node e2e/verify-precompressed-assets.mjs
```

The original `pnpm exec vitest/tsc/vite` commands and default-worker direct Vitest
attempt are retained separately. The Pan-main dependency installation has no
`.bin` and lacks `@asamuzakjp/css-color`; both trees reproduce 347 passing tests,
1 skipped and 182 unhandled environment errors with exit 1. JSON's `success: true`
does not override that exit code. No missing-dependency skip is counted as passed.
The final Vitest runs use the existing healthy `D:/project/Pan/packages/web/node_modules`.
Vitest 2.1.9, Vite 6.4.3, TypeScript 5.9.3, React 19.2.8, jsdom 25.0.1 are the same
in both installations; no installation, lockfile edit or dependency upgrade occurs.

Git inspection:

```powershell
git status --short
git rev-parse HEAD
git show-ref --heads main practical
git merge-base 7f303faa 43d0977f
git rev-list --left-right --count 7f303faa...43d0977f
git merge-tree --write-tree 7f303faa 43d0977f
```

The merge-tree command writes only Git objects, no working-tree merge or ref update.
It exits 1 with the three conflicts listed in the report.
