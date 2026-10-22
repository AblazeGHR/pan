# Retry / parallel scheduling — MA accepted

Candidate d472542a7d4e52f2f49b0a8ebb4b168e9d74ac3d; implementation 1fc9ddc85560d6d60208e956efef0fc2054cbac5. Independently reviewed policy parsing, atomic claim, runner retirement/cancellation, frozen retry qualification, persistent backoff, attempt history, explicit uncertain recovery and frozen query checkpoints.

MA command: canonical Python through scripts/run_tests_isolated.py, pytest tests/test_automation_retry_parallel.py tests/test_automation_retry_http.py tests/test_automation_concurrency.py -o addopts= -q --junitxml=artifacts/retry-parallel/ma-review.xml.
Result: 58 passed, 0 failed/skipped, exit 0, 28.05s. JUnit and actual HTTP evidence are alongside this record. TA 114-case suite is separate, not summed. TA files restored clean after preserving MA outputs.

Accepted defaults: serial; parallel defaults 3 with hard cap32; retries default off, maxAttempts defaults3 including first, hard cap10; exponential backoff starts1s capped60s. Frozen original eligibility and current opt-in both required. Changed Action digest conservatively rejects another effect, including metadata-only changes. Uncertain confirmation is explicit user-authorized repetition of an unknown effect, not exactly-once. No external resource ownership policy is added to Trigger.

Integrated only in isolated integration branch. No main/practical advancement, existing service changes, paid model tests, push or release. React/Job Template/Job snapshot implementation remains subsequent work; human acceptance remains pending.
