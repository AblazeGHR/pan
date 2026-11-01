"""Offline exact-node triage of run 40637381532; never alters test outcomes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def node_path(node):
    module, sep, name = node.partition("::")
    return module.replace(".", "/") + ".py" + sep + name


def classify(row):
    node, text = row["node"], row["text"]
    module = node.split("::")[0].removeprefix("tests.test_")
    if "RetiredEntryPoint" in text:
        return "retired-legacy-protocol", "retired-python-entrypoint", "explicit traceback", "packages/jobs/retired.py::missing; callers must migrate without reopening old execution"
    if "monkeypatcreal_http" in text:
        return "environment-fixture", "fixture-name-typo", "explicit NameError", "tests/test_quota_host_http.py and tests/test_quota_policy_http.py::real_http fixture setup"
    if "tests.test_event_store import setup" in text:
        return "environment-fixture", "removed-test-helper-import", "explicit ImportError", "tests/test_system_fields.py::scenario imports absent tests/test_event_store.py::setup"
    if module in {"background_job_api", "background_job_retry_if", "background_jobs", "job_hidden_production", "job_runner_venv", "legacy_mcp_compat", "scheduler_legacy_bridge", "authorization_runtime"}:
        return "retired-legacy-protocol", "removed-legacy-test-seam", "removed symbol in traceback", "legacy background_jobs/server/system_actions seams; port original assertion to canonical interface with owner review"
    if module == "jobs_api":
        return "retired-legacy-protocol", "legacy-job-http-envelope", "legacy payload/envelope in traceback; exact successor coverage separate", "packages/jobs/api.py legacy writes -> final_api.retired_legacy_write; no mass assertion rewrite"
    if module == "job_window_policy":
        return "retired-legacy-protocol", "old-runner-or-service-test-seam", "legacy runner fake record / removed service imports; success coverage not established", "background_runner.main requires Attempt admission; independent_service no longer exposes subprocess; preserve window proof"
    if module == "independent_job_bridge":
        return "retired-legacy-protocol", "direct-legacy-handler-without-request", "patch_job now delegates authenticated retired_legacy_write", "packages/jobs/api.py::patch_job and final_api.actor require Request; test direct None call is obsolete"
    if module == "interaction_questions":
        return "retired-legacy-protocol", "unbound-retired-automation-host", "Service.start explicit canonical owner requirement", "tests/test_locker_trusted_binding.py::setup must bind canonical Job owner; retain scope/reconciliation assertions"
    if module in {"auto_force_http_e2e", "illegal_queue_wake_http"} and "/api/background-jobs: 401" in text:
        return "retired-legacy-protocol", "unauthenticated-legacy-job-creation", "exact HTTP 401", "owned HTTP fixture must use canonical authenticated admission; do not bypass auth"
    if module in {"agent_inbox_http", "interaction_card_http"}:
        return "environment-fixture", "owned-root-isolation-mismatch", "root containment assertion; agent inbox reproduces locally", "tests/support/agent_inbox_http_server.py roots projection -> packages/core/platform/data_paths.py::job_root; do not weaken containment"
    if module == "illegal_queue_wake_http":
        return "environment-fixture", "owned-server-startup", "startup traceback; not enough proof of baseline-equivalence", "owned launcher/lifespan startup; inspect full traceback before changing fixture"
    if module in {"quota_policy_http", "job_retirement_quota_transport"}:
        return "retired-legacy-protocol", "old-quota-owner-fence-expectation", "exact existing mapping in evidence/quota-native/PROCESS_HTTP.md", "canonical once admission / account-unknown monitor; preserve original red and unbound fence"
    groups = {
        "settings_ui": ("ui-default-contract-drift", "packages/core/config.py::DEFAULT_CONFIG and server UI defaults vs tests/test_settings_ui.py::_UI_DEFAULTS"),
        "session_incremental": ("history-terminal-projection", "packages/core/worker.py::_persist_terminal_state / terminal history vs exact raw history assertion"),
        "persistence_sharding": ("history-terminal-tail-contract", "worker._persist_terminal_state / session.history terminal row content vs assistant tail assertion"),
        "canonical_form_metadata": ("canonical-native-action-schema", "product_session_message Action inputSchema/source fields vs tests/test_automation_canonical.py::job_def"),
        "scheduler_structured_browser": ("canonical-native-action-schema-or-browser", "tests/test_scheduler_structured_browser.py fixture Action arguments and structured UI locators; distinguish parameter cases"),
        "scheduler_management_browser": ("canonical-admission-envelope", "owned management browser fixture reads result after canonical admission; inspect HTTP error body"),
        "automation_concurrency": ("provider-observation-seam", "tests/test_automation_concurrency.py::execute_both and SessionProvider.calls; retain real effect/cancel assertions"),
        "job_retirement_closure_successors": ("edited-run-admission-skipped", "FinalService.create_run/final runtime concurrency state: expected 202, actual 200 skipped after first dispatch"),
        "main_service_exit": ("canonical-lifecycle-transition", "server._canonical_lifecycle_handoff and legacy find_service_job snapshot/duplicate semantics; preserve shutdown constraints"),
        "main_service_restart": ("canonical-lifecycle-transition", "server._canonical_lifecycle_handoff supervisor launch timing and old service registry observation; preserve stop-before-start proof"),
        "legal_offline_http": ("owned-host-exit-timeout", "host_server.PanServer + canonical lifecycle supervisor; timeout cannot be labeled obsolete without lifetime proof"),
        "job_lifecycle_prevention": ("controller-tree-not-created", "tests/helpers/lifecycle_controller_host.py and legacy/independent runner start; missing tree alone does not prove retirement root cause"),
    }
    if module in groups:
        group, proposal = groups[module]
        return "new-regression-candidate", group, "actual remote failure; product vs test adaptation unresolved (not claimed newly introduced)", proposal
    raise AssertionError(f"Unclassified node: {node}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", type=Path, required=True)
    args = parser.parse_args()
    source = args.remote / "failure-inventory.json"
    raw = source.read_bytes()
    inventory = json.loads(raw)
    mappings = json.loads((ROOT / "evidence/job-retirement-closure/successors.json").read_text(encoding="utf-8"))
    mapped = {r["node"]: r for r in mappings["originalNodes"]}
    quota = {
        "tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown",
        "tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http",
        "tests/test_job_retirement_quota_transport.py::test_quota_real_tcp_cas_reopen_and_legacy_writer_fence",
    }
    reports = []
    for job in inventory:
        if "pytest-results" not in job["file"]:
            continue
        xml_path = args.remote.joinpath(*job["file"].split("\\"))
        tree = ET.parse(xml_path)
        xml_records = sum(len(c.findall("failure")) + len(c.findall("error")) for c in tree.getroot().iter("testcase"))
        assert xml_records == len(job["failures"])
        rows = []
        for index, row in enumerate(job["failures"]):
            category, group, confidence, proposal = classify(row)
            mapping = mapped.get(node_path(row["node"]))
            successor = None
            if mapping:
                successor = {"reference": "evidence/job-retirement-closure/successors.json",
                             "originalStatus": mapping["status"],
                             "strictSuccessors": mapping.get("strictSuccessors", []),
                             "boundary": mapping.get("boundary"),
                             "claim": "existing mapping only; no new coverage acceptance or historical green claim"}
            if node_path(row["node"]) in quota:
                successor = {"reference": "evidence/quota-native/PROCESS_HTTP.md#exact-successor-mapping",
                             "strictSuccessors": ["tests/test_quota_native_admission.py::test_unknown_production_process_restart_real_http"],
                             "claim": "existing documented mapping; original red preserved"}
            rows.append({**row, "recordIndex": index, "category": category, "group": group,
                         "classificationBasis": confidence, "smallestOwnerProposal": proposal,
                         "existingSuccessorMapping": successor, "resolvedByThisCandidate": False})
        reports.append({"artifact": job["file"], "xmlSha256": hashlib.sha256(xml_path.read_bytes()).hexdigest(),
                        "testcaseRecords": job["nodes"], "failureErrorRecords": len(rows),
                        "skipped": job["skips"], "categories": dict(Counter(r["category"] for r in rows)),
                        "groups": dict(Counter(r["group"] for r in rows)), "records": rows})
    assert [r["failureErrorRecords"] for r in reports] == [243, 242]
    payload = {"runId": 40637381532, "headSha": "5adbf3662b8e06e8eae13bf4aa5320a336bfa7a7",
               "sourceInventorySha256": hashlib.sha256(raw).hexdigest(),
               "categoryMeaning": {"retired-legacy-protocol": "failure invokes or expects removed legacy protocol; NOT proof of strict successor completion",
                                   "new-regression-candidate": "unresolved current contract/product behavior; NOT proof introduced by this patch or baseline-equivalent",
                                   "environment-fixture": "concrete fixture/import/isolation problem; not dismissed or suppressed"},
               "jobs": reports}
    (OUT / "windows-node-classification.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "remote-failure-inventory.json").write_bytes(raw)
    print(json.dumps([{k: v for k, v in r.items() if k != "records"} for r in reports], indent=2))


if __name__ == "__main__":
    main()
