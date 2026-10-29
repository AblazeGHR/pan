# Independent candidate CI proposal (MA controls push/dispatch)

Use exact clean candidate rooted at main9b4506, not release4a86 or JobTA ref.
Run Windows Python3.12 +3.14, Ubuntu/macOS Python3.12. Keep all failures/skips and complete logs.

Dependencies in the disposable CI checkout only:

```
python -m pip install -r minimal-requirements.txt -r packages/qq/requirements.txt pytest pytest-timeout playwright==1.63.0
python scripts/validate_minimal_platform.py --phase import --output evidence/minimal-ci
python scripts/validate_minimal_platform.py --phase collect --output evidence/minimal-ci
python scripts/validate_minimal_platform.py --phase fast --output evidence/minimal-ci
```

Full collect retains tests/, packages/qq/, packages/wechat/. The baseline Windows ACL
declarations test-only guard is included with all Windows helper/test AST unchanged.
FAST exact node list is versioned in scripts/validate_minimal_platform.py and includes
the two original recurring plugin nodes via their full suites. Do not increase waits,
skip or suppress timeout errors. POSIX runtimeOwner/hidden-desktop tests from4a86 are
not part of this minimum platform feature; registry processOwner remains false.

Fresh authenticated production browser job, Ubuntu at minimum (Windows useful):

```
# Node24 / pnpm9.7.0, only inside disposable checkout:
cd packages/web
pnpm install --frozen-lockfile
pnpm exec vitest run src/components/session/NewSessionModal.test.tsx src/stores/platformStore.test.ts src/components/layout/viewEntries.capability.test.ts src/views/TerminalPanel.capability.test.tsx src/components/layout/AppSettingsModal.test.tsx
pnpm exec tsc -b --pretty false
pnpm build
cd ../..
python -m playwright install --with-deps --only-shell chromium
python scripts/validate_minimal_platform.py --phase browser --output evidence/minimal-ci
```

Set PLAYWRIGHT_BROWSERS_PATH consistently for both installation and test processes.
Upload evidence/minimal-ci and evidence/cp-x3-minimal-20261029 generated fixture evidence,
fresh dist hashes/build log, exact source SHA and dependency lock hashes; do not accept
checked-in old JUnit as new run output. Browser fixture uses real app/production bundle,
isolated authenticated cookie, random port/data and inert CLI inventory; policy injection
checks explicit false and probe-failure behavior and is labelled as such. No actual model
or provider account is used. Ordinary pipe real child and synthetic existing Job/Scheduler
profile compatibility are in FAST. Real data migration is neither needed nor performed.

No TA remote run is authorized. Local Windows success does not accept this source on
Ubuntu/macOS; earlier4a86 green is not inherited. Full legacy suite remains independently
reportable; do not blanket-xfail it or promise unrelated baseline failures are fixed.
