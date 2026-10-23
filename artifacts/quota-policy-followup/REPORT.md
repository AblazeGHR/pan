# Automatic quota strategy follow-up

- Tree: `D:/project/pan-worktrees/event-trigger-core-20261022`
- Baseline: `71ffbed3282d42cf46196bfc27efb5f0e43c63ad`
- Scope: synthetic quota/event classification, policy persistence, switching,
  restart/wake orchestration and deterministic fake regressions only.
- Private exporter/OAuth/keyring code remains default-off and is not imported by
  `packages/automation/quota_policy.py`; this batch does not make it a strategy
  dependency.

## Verification

Command (isolated runner, default Windows cp936 environment, no PYTHONUTF8):

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_runtime_issues.py tests/test_quota_policy.py tests/test_quota_policy_safety.py tests/test_quota_policy_idle.py tests/test_quota_host.py tests/test_quota_host_http.py -q --junitxml=artifacts/quota-policy-followup/quota-policy-followup.xml
```

Result: **197 passed, 0 failed, 0 errors, 0 skipped; exit 0**. JUnit is
[quota-policy-followup.xml](D:/project/pan-worktrees/event-trigger-core-20261022/artifacts/quota-policy-followup/quota-policy-followup.xml).

Coverage includes:

- structured native `codex_quota_exhausted`, `codex_capacity`, and
  `cbc_rate_limited` classification; generic text/tool failures remain unknown;
- durable deduplication, policy revision/mode persistence, uncertain recovery,
  capacity retry-once, temporary exclusion, and no duplicate effects;
- five-hour/weekly reset ordering, lowest reset then usage ranking, permanent
  disabled IDs independent from temporary capacity exclusion, and the unique
  future reset Job when no eligible candidate exists;
- 95% monitor Job uniqueness, stale/unknown query behavior, manual/close fences,
  and no implicit wake from unknown data;
- freeze of live Codex Workers and unfinished running TA task identity, switch
  preflight rechecks, successor generation/PID/birth/epoch/account validation,
  exactly-once TA wake, MA resume Job before final MA restart, and negative cases
  for completed/idle/replaced/unknown identities;
- host effect CAS, cancellation/close, persistence failure, uncertain receipt,
  no long unrelated Session lock, and owned HTTP/queue fake lifecycle.

## Boundaries

No real account, credential, cc-switch mutation, user Worker, paid model, 8767,
8768, existing service, main/practical/integration tree, push or release was
used. The quota policy still fails closed on unknown or stale observations and
never turns an exporter capability into an automatic effect. Production Bridge
reads/effects remain adapter contracts; these tests use synthetic adapters only.
Human and MA acceptance remain separate.
