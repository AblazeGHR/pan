# Independent Job engine implementation audit

Date: 2026-10-09

## Implemented source

- `packages/jobs/independent_service.py`: loopback stdlib HTTP host, explicit
  absolute data root, SQLite registration and outbox, idempotent create, query,
  cancel request, retry, event read and ack.
- `packages/jobs/independent_runner.py`: detached one-run subprocess, sanitized
  task environment, command log, runner/task identity persistence, atomic
  terminal plus outbox publication.
- `packages/jobs/independent_client.py`: stdlib API client matching the bridge
  contract, including `None` on GET 404.

## Evidence and limits

The targeted test modules are `tests/test_independent_job_service.py` and
`tests/test_independent_job_bridge.py`. The implementation does not edit existing background-job/config/skill
code. No production installation, scheduler, legacy migration, Session queue
integration, service-manager registration, remote authentication, or real user
acceptance is claimed here.

## Run record

- Interpreter: `D:/project/Pan/.venv/Scripts/python.exe`
- Command: `D:/project/Pan/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan/.venv/Scripts/python.exe -m pytest tests/test_independent_job_service.py tests/test_independent_job_bridge.py -o addopts= -q`
- Exit: 0
- Output: see `test-output.md` (`24 passed in 16.18s`).
- Coverage includes bridge routing, idempotent concurrent create, atomic immediate-result/outbox ack, malformed JSON, verified owned-tree cancellation, wrong runner identity cancellation refusal, UNKNOWN retry refusal, task environment stripping, same-runner recovery across listener restart, and a Windows `taskkill /T` sibling-host survival check.
- The result covers this local Windows test run only. It does not establish a deployed service host or production integration.
