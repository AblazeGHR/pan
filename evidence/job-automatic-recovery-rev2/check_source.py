"""Offline source characterization only; no imports of product/runtime code."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "aa76b83e16ac580d8a548752011b7c81636c6519"
TARGETS = {
    "packages/core/background_retry.py": ["policy", "defaults", "terminal", "new_attempt"],
    "packages/core/background_jobs.py": ["runner_update", "reconcile_running", "retry_due"],
    "packages/core/background_runner.py": ["main", "_run_legacy"],
    "packages/jobs/final_contracts.py": ["_job_step_defs", "_normalize_job"],
    "packages/jobs/final_service.py": ["AggregateStore.mutate", "FinalService.transaction",
        "FinalService._references", "FinalService._admit", "FinalService.control_run",
        "FinalService._guard_effect", "FinalService.dispatch_once", "FinalService._drive_run",
        "FinalService._settle", "FinalService.recover", "FinalService._cleanup_complete",
        "FinalService.recover_external_receipts", "FinalService.tick", "FinalService.start"],
    "packages/jobs/final_runtime/coordinator.py": ["FinalRuntime.compare_and_swap", "FinalRuntime.metadata"],
    "packages/jobs/final_runtime/ledger.py": ["aggregate", "Ledger.admit", "Ledger.start",
        "Ledger.begin_attempt", "Ledger.admit_effect", "Ledger.record_effect"],
    "packages/jobs/independent_executor.py": ["ExecutionStore.admit", "ExecutionStore.cancel",
        "IndependentExecutor.lookup_execution", "IndependentExecutor.execute"],
    "packages/actions/typed_executor.py": ["TypedExecutor._process"],
    "packages/scheduler/creators.py": ["CreatorStore.transaction"],
    "tests/test_background_job_retry_if.py": ["test_reason_match_matrix",
        "test_zero_budget_and_spawn_failure_consume_no_extra_attempt",
        "test_restart_reconcile_persists_count_backoff_and_limit",
        "test_cancel_during_backoff_prevents_retry", "test_accepted_cancel_beats_orphan_recovery",
        "test_concurrent_recovery_and_late_runner_are_fenced", "test_orphan_does_not_duplicate_live_task",
        "test_separate_recovery_process_after_runner_loss"],
    "tests/test_authorization_runtime.py": ["test_automatic_recovery_revocation_keeps_budget_and_never_starts_another_attempt"],
    "tests/test_automation_retry_parallel.py": ["test_canonical_retry_keeps_run_and_effect_key_only_failed_step_repeats",
        "test_unknown_or_cleanup_unconfirmed_never_auto_retries_or_confirms",
        "test_retry_requires_frozen_idempotence_and_cleanup_contract"],
    "tests/test_job_final_independent_execution.py": ["test_fe02_retry_keeps_job_run_step_effect_with_new_attempt_history",
        "test_fe02_retry_needs_cleanup_proof", "test_fe02_explicit_reconciliation_queries_original_receipt_without_new_attempt"],
    "tests/test_job_final_http_service.py": ["test_ft01_revoke_between_steps_prevents_next_effect",
        "test_ft01_unknown_is_not_retried_or_replayed_on_restart",
        "test_ft02_unbound_automatic_recovery_is_explicit_unsupported"],
}


def main():
    records, texts, symbols = {}, {}, {}
    for name, requested in TARGETS.items():
        raw = (ROOT / name).read_bytes()
        text = raw.decode("utf-8-sig")
        texts[name] = text
        tree = ast.parse(text)
        found = {}

        def visit(node, parents=()):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    key = ".".join((*parents, child.name))
                    found[key] = child
                    visit(child, (*parents, child.name))
                else:
                    visit(child, parents)

        visit(tree)
        anchors = {}
        for symbol in requested:
            node = found[symbol]
            anchors[symbol] = {"start": node.lineno, "end": node.end_lineno}
            symbols[name + "::" + symbol] = ast.get_source_segment(text, node)
        canonical = subprocess.check_output(["git", "show", BASELINE + ":" + name], cwd=ROOT)
        # Windows checkout CRLF does not constitute a product edit.
        same = raw.replace(b"\r\n", b"\n") == canonical.replace(b"\r\n", b"\n")
        assert same, name + " differs from accepted source"
        records[name] = {"sha256Checkout": hashlib.sha256(raw).hexdigest(),
                         "baselineEquivalentIgnoringCRLF": same, "symbols": anchors}

    svc = "packages/jobs/final_service.py::FinalService."
    checks = {
        "automatic_unconditionally_rejected": '"capability_unsupported"' in symbols[svc + "_references"],
        "admit_does_not_freeze_recovery": '"recovery"' not in symbols[svc + "_admit"],
        "manual_retry_preserves_frozen_action_contract": 'policy["requiresProof"]' in symbols[svc + "control_run"],
        "manual_retry_has_no_job_maxAttempts_limit": "maxAttempts" not in symbols[svc + "control_run"],
        "driver_does_not_check_backoff_deadline": "eligibleAt" not in symbols[svc + "_drive_run"],
        "driver_new_attempt_current_owner": "owner_epoch=self.owner.token" in symbols[svc + "_drive_run"],
        "driver_reuses_retry_generation": 'retry=attempt["generation"] > 1' in symbols[svc + "_drive_run"],
        "frozen_authority_rechecked": 'frozen["authority"]' in symbols[svc + "_guard_effect"],
        "pulse_existing_quarter_second": "asyncio.sleep(0.25)" in symbols[svc + "start"],
        "recovery_marks_host_loss_unknown": '"host_restarted"' in symbols[svc + "recover"],
        "external_recovery_has_no_execution_submit": ".execute(" not in symbols[svc + "recover_external_receipts"],
        "cleanup_requires_supervisor_exit": "supervisorExitProof" in symbols[svc + "_cleanup_complete"],
        "runtimeMetadata_already_exists": 'job.get("runtimeMetadata"' in symbols["packages/jobs/final_runtime/coordinator.py::FinalRuntime.metadata"],
        "executor_retry_checks_cleanup": 'get("cleanupProof", {}).get("confirmed") is True' in symbols["packages/jobs/independent_executor.py::ExecutionStore.admit"],
        "pending_is_currently_unknown": 'EffectReceipt(dict(effect_key), "unknown"' in symbols["packages/jobs/independent_executor.py::IndependentExecutor.execute"],
        "old_extra_retry_formula": 'job["maxRetries"] - (job["attempt"] - 1)' in symbols["packages/core/background_retry.py::defaults"],
        "old_reason_enum_six": all('"' + reason + '"' in texts["packages/core/background_retry.py"] for reason in
            ("failed", "orphaned", "timeout", "interrupted", "cancelled", "dependency-unavailable")),
    }
    assert all(checks.values()), checks
    old_sha = subprocess.check_output(["git", "rev-parse", "7385f807"], cwd=ROOT, text=True).strip()
    historical = {}
    for name, requested in {
        "packages/core/background_jobs.py": ["start", "retry_due", "runner_update", "reconcile_running"],
        "packages/core/background_retry.py": ["policy", "terminal", "new_attempt"],
    }.items():
        raw = subprocess.check_output(["git", "show", old_sha + ":" + name], cwd=ROOT)
        text = raw.decode("utf-8-sig")
        functions = {n.name: n for n in ast.parse(text).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        historical[name] = {"sha256GitBlobBytes": hashlib.sha256(raw).hexdigest(),
            "symbols": {n: {"start": functions[n].lineno, "end": functions[n].end_lineno} for n in requested}}
        if "start" in requested:
            body = ast.get_source_segment(text, functions["start"])
            assert "_retry.policy(retry_if, max_retries, backoff_sec" in body
            assert '"attempt": 1' in body
    result = {"sourceSHA": BASELINE, "kind": "offline-source-characterization",
              "historicalRetryIfSHA": old_sha, "historicalSource": historical,
              "productChangesImplemented": False, "pytestRunsThisBatch": 0,
              "files": records, "checks": checks,
              "counts": {"sourceFiles": len(records), "symbolAnchors": len(symbols),
                         "checksPassed": sum(checks.values()), "checksFailed": 0},
              "unverified": ["automatic-runtime", "provider-effects", "real-data-migration",
                  "browser", "three-platform-runtime", "remote-CI", "deployment"]}
    target = Path(__file__).with_name("source-review.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
