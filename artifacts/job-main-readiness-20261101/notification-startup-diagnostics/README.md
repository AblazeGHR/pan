# Notification browser startup failure: diagnosis in progress

Original remote run https://github.com/AblazeGHR/pan/actions/runs/41406716824 is completed failure on cd19311cde5f3a2c90be9650407cec2b1cc6ebc0.
Python 3.14 has exactly one failed node: tests/test_notification_kind_http.py::test_shared_kind_exclusions_browser_and_background; initial App settings button was not visible within the existing 30000ms Playwright deadline. Other seven jobs succeeded. Original complete JUnits and failed log are preserved separately; no assertion is removed or reclassified.

The initial original node passed locally once (17.03s); six fresh instances with added diagnostics passed (88.63s). Ordered adjacent eight-file suites passed 176 tests per Python version, no skips: 3.14 52.828s and 3.12 35.104s; source drift is empty. These results do not identify the remote root cause or resolve the original red.

The prepared two-file patch adds entry HTTP status assertion, failed requests/status/console/page error evidence, bounded failure DOM and screenshot, and carries the JSON into the original pytest assertion/JUnit. No product change, navigation retry, timeout/budget change, bypass, skip or xfail. It is not applied to the outer source while original full job_638138545101005c8589ccad remains frozen. Failure injection controls are diagnostic negatives only and never ship.

Pending: verify both negative-control results; apply/normal-hook commit diagnostic patch after original full terminal and own generated-output preservation; obtain fresh exact-SHA complete CI with original failure kept. Diagnose any repeat from captured failure context. A later green does not retrospectively turn the original red into success or establish an unproven root cause.

Original local complete job_638138545101005c8589ccad verified completed/exit0/treeEnded/notification delivered: 8588 total, 8492 passed, 96 existing skips, no failures/errors, 4969.001s, sourceDrift[]. 104 tracked and 10 own untracked generated files hash-verified backed up privately and restored. The two diagnostic failure controls both produced expected pytest exit1 with original 30s button assertion and distinct failure context, clean diagnostic source restored afterward. These controls identify diagnostic capability only, not the original CI root cause. Final SHA requires its own full remote CI.
