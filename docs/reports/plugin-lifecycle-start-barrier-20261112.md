# Plugin lifecycle startup barrier CI repair

Base: `49bbb93776e71e49e42ca97bdf4ad32a8c15edd8`. Branch: `fix/plugin-lifecycle-start-barrier-20261112`. The original integration branch and evidence are preserved.

CI 43169931281 / Python 3.12 failed at `within(entered)` in the events.ready stage, before calling close. Original XML: `D:/project/Pan/.workflow/practical-main-push-ci-43169931281/artifacts/attempt-1/pytest-results-3.12/pytest-results.xml`.

The readiness wait covers platform data-directory setup, Windows Python stdio runner spawn, Node startup, provider.initialize, events.initialize, plugin_ready, and events.ready. It does not measure whether close or another lifecycle remains nonblocking. The previous .5s readiness deadline conflated those requirements.

Only the real-start intersection test now uses a bounded 10-second readiness wait, matching the existing real-provider `until` setup budget. Global `within` stays .5 seconds, all other nonblocking deadlines stay unchanged, and close completion stays 2 seconds. No product runtime timeout or implementation changed.

All five stages run both normal and deliberately held startup. A spawn barrier proves the original .5-second wait expires before any process exists; after releasing it an injected .6-second startup delay deterministically reproduces the old deadline failure. The wrapper then creates the real process and runs real handshakes. Every stage retains ready suppression, empty connections/runners/cleanups, no provider ready record, no plugin handles, and PID/create-time verified owned-process exit assertions. Finally releases both barriers even on failure.

Red Job `job_b0687644e0b3ce0a55b0dc1a`: exit 1, one events.ready-held-start failure at the original readiness deadline, before close. Evidence `.tmp/practical-main/plugin-barrier-red.xml` and original Pan Job log.

Green Job `job_6d42c5a6e60242096ce99dd5`: exit 0, 33 passed / 0 failures / 0 skips. Command (repository root, provisioned Python 3.14):

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_plugin_event_lifecycle.py tests/test_plugin_events.py -q --junitxml=.tmp/practical-main/plugin-barrier-green.xml
```

Local Windows evidence does not replace Python 3.12 / remote exact-SHA CI. MA owns remote verification and promotion. No main/practical ref changes, push, CI rerun, dispatch, or service restart performed by this TA.
