# Original exact 1cec GitHub CI terminal

Run https://github.com/AblazeGHR/pan/actions/runs/41356342496 completed failure, head_sha1cec53a16193c7930b6097b942110f473bcc7036. Original native watcher job_b2d7be369a031eb4b472c792 failed1 and is terminal; do not restart it. Current own candidatefa44d707 is later/unpushed and cannot use this run as acceptance.

| Job | Terminal | Complete JUnit |
|---|---|---|
| pytest3.12 | failure |8574tests23fail0error102skip|
| pytest3.14 | failure |8574tests23fail0error103skip|
| platform-fastUbuntu3.12 | failure |456tests2fail0error23skip; collect0errors|
| platform-fastmacOS3.12 | failure |456tests2fail0error21skip; collect0errors|
| platform-fastWindows3.12 | success |run job124997087537|
| windows-owned3.12 | success |run job124997087570|
| windows-owned3.14 | success |run job124997087298|
| Ubuntu real authenticated production HTTP/browser3.12 | success |run job124997087667|

Four failing artifacts downloaded completely and retained, plus exact terminal job IDs/headSha/status and50failure observations in ci-41356342496-failures.json. Full gh --log-failed is also retained privately in .validation/readiness/ci-41356342496-failed.log. All original failures remain intact.

Both Windows full jobs: local known routecount/pathsnapshot/name-trim/crashfixture/schedulerroot groups recur; pagination reports timeout there, emphasizing need to keep full regression. Two extra CI failures are exact historical gitshow evidence dependencies absent in shallow checkout: test_job_migration_pending_adapter_contract requires9b4506c94ac67a39093961b218b2f01d387d1049 core/background_jobs.py; test_job_recovery_stable_reset requires4b577633d2d9ffcdc5bf8a44b12836724c18804e final_service.py/action_runtime.py. Correct checkout history completeness; never skip or weaken these original-baseline contracts.

Both POSIX fast jobs fail the same2nodes:

1. test_job_posix_safety.test_product_default_detached_executor_keeps_original_success_receipt unpacks terminal effects immediately after dispatch. Known native execution-pending now correctly retains a running Attempt/effect admission and effects empty, as original approved pending contract. Successor must bind original effectAdmissions identity while running, await original native receipt, then retain every original terminal receipt/cleanup/supervisor/identity/one-execution assertion. This is not permission to accept either incomplete or unknown as terminal success.
2. test_job_process_logs.test_large_unicode_log_leader_loss_remains_unknown_and_original_queryable GETs a stale running revision before cancel; control's original receipt recovery observes native unknown first, yielding legitimate409revision_conflict/currentRevision2. Synchronize/query original native receipt through existing recover_external_receipts before reading current uncertain Run and explicit cancel; retain exact original Attempt/log/effect/no-replay/subtree cleanup invariants, no production CAS relaxation or timeout masking.

Sourcefreeze: current local persistentjob_7bf42c4f29806def1cb89d74 runs exactfa44 allaffected local groups. No test/product/CI source changes while it runs; above are concrete next fixes to apply only after its own terminal. Then necessary same-interface checks and new exactSHAfullCI. No duplicate CI watcher, no foregroundpolling; old remote run already terminal.

main/practical/realservices/source8unknownleases untouched. This report is original run failure identity evidence, not CIgreen or MAacceptance.
