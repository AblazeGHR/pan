# CLI automatic registration candidate

Base: 0156f008b149d59641ccc0176721c8e2b5ae59d8
Branch: feature/cli-auto-register-20261023

## Entry audit and behavior

- Windows setup.bat prepares its interpreter/dependencies, then invokes the automatic registration hook. start_pan.bat invokes the same hook before the internal launcher, using its bootstrap interpreter (.venv when available, otherwise PATH python).
- README Windows installation now ends with start_pan.bat. Clone alone cannot execute registration. Direct pip installation, main.py, internal launcher, POSIX setup/start, imports and Workers do not register.
- Auto defaults to LOCALAPPDATA/Pan/bin, reuses owned UTF-8 PowerShell/ASCII CMD shims and manifest, writes only HKCU PATH, verifies readback and broadcasts environment changes. New terminals inherit the change; existing environments are untouched.
- Git invocation and practical branch restriction removed. Checkout/interpreter ownership checks remain. Conflicts in process PATH or persistent user PATH, another checkout/interpreter, unowned/modified shims and permissions failures warn; startup continues. Explicit maintenance commands still fail nonzero.
- Repeated matching registration is idempotent. Uninstall verifies owned bytes and only removes a PATH entry added by registration. Failed PATH removal retains owned files. Pre-existing entries survive uninstall. No service/admin/OAuth integration.

## Validation

Command (exit 0):
`python scripts/run_tests_isolated.py -- python -m pytest tests/test_pan_cli.py tests/test_launcher.py -q -o addopts= --junitxml=artifacts/cli-auto-register/targeted.xml`

37 passed, zero skipped, one existing pytest warning: unknown timeout option (pytest-timeout unavailable). See targeted.log and targeted.xml.

Registration tests inject in-memory PATH storage, temporary directories and stub notifications. Batch integration executes cmd.exe from a temporary Chinese/space checkout with isolated LOCALAPPDATA and file-backed fake HKCU storage; a fixture launcher opens no listener. Success and denied writes both reach the launcher and preserve its exit code 7. Coverage includes no Git, multiple checkouts, duplicate registration, foreign commands, edited files, directory/HKCU permissions, rollback and uninstall.

Additional documentation check (exit 1): 2 passed, 1 failed, 1 skipped. Existing failure: docs/skills/pan/references/http-api.md:372 uses child-agent. Reproduced by reading baseline 0156f008 Git objects (baseline-terminology.txt); unrelated source left untouched. Skip: absent gitignored skill sync copy. See docs-terminology.log/xml.

`git diff --check` passed (exit 0).

## Boundaries

No real HKCU/PATH writes, environment broadcasts, real service startup, 8767/8768 operations, main/practical changes, push or deployment. No full setup dependency installation or real Windows shell refresh acceptance performed. The batch hook is exercised with fake storage/launcher; production registry API and actual service readiness were not exercised by this task. POSIX does not gain a Windows command registration. Concurrent external registry edits have the existing pre-write comparison/readback protection, not an atomic registry transaction. Final SHA is supplied in the TA delivery after commit; this is a candidate awaiting MA acceptance.
