# Partial follow-up on integrated source 818f6b84

Remote evidence source: c9c9fe92160bd257eb4b7d53b0669fa85aa1cf2a, run 39961776834.
Python 3.12: 5456 passed, 3 failed, 61 skipped; Python 3.14: 5459 passed, 61 skipped.
Both skip multisets match the original baseline. All artifacts were collected without errors.

The three failures were delayed-restart metadata PermissionError, source-aware auto-report PUT settings 500, and user-Locker owned server returncode 1. The complete JUnit and job logs are retained here. The archive contains no isolated profile pan.log or shutdown decision record. Consequently the settings traceback and whether the Locker test called terminate cannot be recovered from this archive. Usage enrichment and Proactor warnings do not establish either root cause.

On 818f6b849d31cac98f03766ad1a6eb7788f5ea58 the three affected files passed locally (7 tests). Commit cdcbd4dd8736ca12984329154450a249ed86856d fixes an independently reproduced metadata-reader defect: errno EACCES without winerror was excluded from its bounded Windows open retry. Red injection and green JUnit are retained; this does not establish the sole cause of the remote failure.

The subsequent test-only diagnostics retain the owned server log on HTTP errors and record returncode before cleanup, the exit API response, any wait error, forced terminate, and returncode after cleanup. The owned exit route dumps thread stacks at 15 seconds if shutdown stalls. The existing 20-second shutdown wait and exit-code-zero assertion are unchanged.

Diagnostic validation Job job_7a4578d3d5d9c255b3c52dd9 completed exit 0: four tests passed, no skips. Both HTTP/MCP and browser Locker instances recorded a live process before cleanup, an ok exit response, no forced terminate, and returncode 0. This validates the diagnostic path, not the old intermittent failures. Settings PUT500 and Locker exit1 remain unresolved pending actionable failure evidence.

Remote 818f6b84 run 39965883287 has its separate collector job_f95ddbffe6882dab01aa6650; its gate is not inferred from the above results. No rerun, push, main/practical movement, real service, account, provider, or QQ operations occurred.
