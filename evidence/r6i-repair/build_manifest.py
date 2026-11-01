"""Build review indexes from retained outputs; does not run or mutate product."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
BASE = "bac726cf84c0510dff8618afb82e227e660f590c"
PREVIOUS = "0cc627057d6dc3ee8be5a7690c2a979af9e614f3"
def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()
SOURCE = git("rev-parse", "HEAD")
assert SOURCE == "be4d91076b854f3edcd91b7a77fb3e1eae3aadfa"
assert git("branch", "--show-current") == "r5/p21-r6-time-events-20261025"
subprocess.run(["git", "merge-base", "--is-ancestor", BASE, SOURCE], cwd=ROOT, check=True)
selected = ["backend-final-v2", "surface-final", "consumers-final-v2", "retained-contracts-final",
            "scheduler-remaining-final", "original-red-current", "repair-delta-final",
            "strengthened-final", "startup-final", "browser-final-v4"]
matrices = {}
final = {}
for label in selected:
    tests = list(ET.parse(OUT / (label + ".xml")).iter("testcase"))
    counts = Counter()
    for test in tests:
        status = next((state for element, state in (("failure", "failed"), ("error", "error"),
            ("skipped", "skipped")) if test.find(element) is not None), "passed")
        counts[status] += 1
        node = test.attrib["classname"].replace(".", "/") + ".py::" + test.attrib["name"]
        final[node] = {"node": node, "status": status, "evidence": label + ".xml"}
    matrices[label] = {"tests": len(tests), **{state: counts[state] for state in ("passed", "failed", "error", "skipped")},
                       "exit": 1 if counts["failed"] or counts["error"] else 0}
total = {state: sum(row["status"] == state for row in final.values()) for state in ("passed", "failed", "error", "skipped")}
assert len(final) == 1275 and total == {"passed":1232,"failed":43,"error":0,"skipped":0}
native_nodes = re.findall(r"^\s*✓ (.+?) > (.+)$", (OUT / "vitest-final-v4.log").read_text(encoding="utf-8"), re.M)
assert len(native_nodes) == 77
changed = []
for line in git("diff", "--name-status", BASE, SOURCE, "--", "packages", "scripts", "tests", "docs").splitlines():
    status, path = line.split("\t")
    data = (ROOT / path).read_bytes()
    changed.append({"status":status,"path":path,"gitBlob":git("rev-parse", SOURCE + ":" + path),
                    "worktreeSha256":hashlib.sha256(data).hexdigest()})
repair = git("diff", "--name-status", PREVIOUS, SOURCE).splitlines()
assert not any(line.startswith("D\t") for line in repair)
unchanged = ["evidence/r6i-resume", "evidence/r6i-time-events", "docs/reports/R6_I_SOURCE_CUTOVER_20261027.md",
    "docs/reports/R6_I_TIME_EVENTS_20261026.md", "packages/scheduler/legacy_bridge.py", "packages/scheduler/store.py",
    "packages/scheduler/time_events.py", "packages/jobs/runtime_owner.py", "packages/jobs/independent_executor.py",
    "packages/jobs/independent_runner.py", "packages/jobs/independent_service.py"]
assert git("diff", "--name-only", PREVIOUS, SOURCE, "--", *unchanged) == ""
def finish_ast(commit):
    tree = ast.parse(git("show", commit + ":packages/jobs/final_runtime/ledger.py"))
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "finish_attempt")
    return ast.dump(method, include_attributes=False)
assert finish_ast(BASE) == finish_ast(SOURCE)
cmd_files = {
    "backend-final-v2": "test_job_attempt_epoch test_scheduler_creator_management test_scheduler_source_migration test_scheduler_dependency_cutover test_scheduler_pending_adoption test_scheduler_time_events test_scheduler_canonical_migration test_scheduler_cron test_job_final_creators test_job_runtime_owner test_job_runtime_registry test_job_final_runtime test_job_final_http_service test_job_final_independent_execution test_job_final_transports test_job_final_clients test_automation_canonical test_automation_job_links",
    "surface-final": "test_scheduler_creator_management test_job_path_flip test_authorization_permissions test_authorization_runtime test_authorization_mcp test_scheduler_surface_retirement test_scheduler_host_retirement test_scheduler_api test_scheduler_history_read test_scheduler_store test_scheduler_dispatch test_job_delete_safety",
    "consumers-final-v2": "test_job_path_flip test_quota_policy test_quota_policy_safety test_quota_policy_idle test_quota_policy_http test_jobs_completed_retention test_data_retention test_handoff_job_retarget test_session_handoff test_test_isolation test_job_final_contracts",
    "retained-contracts-final": "test_quota_host test_automation_job_links test_scheduler_legacy_bridge test_jobs_api test_authorization_runtime test_quota_policy_http",
    "scheduler-remaining-final": "test_scheduler_engine test_scheduler_integration test_scheduler_mcp test_independent_composition_observations",
    "repair-delta-final": "test_job_attempt_epoch test_scheduler_creator_management",
    "strengthened-final": "test_job_attempt_epoch test_job_retained_pin_successor",
    "startup-final": "test_scheduler_dependency_cutover",
    "browser-final-v4": "test_scheduler_management_browser",
}
commands = []
for label, filenames in cmd_files.items():
    py = ".validation/r6i-venv/Scripts/python.exe" if label.startswith("browser") else "python"
    argv = [py,"scripts/run_tests_isolated.py","--",py,"-m","pytest",*["tests/"+name+".py" for name in filenames.split()],
            "-q", *( ["-s"] if label == "startup-final" else ["--tb=short"] ),
            "--junitxml=evidence/r6i-repair/"+label+".xml"]
    commands.append({"label":label,"argv":argv,"log":"evidence/r6i-repair/"+label+".log","cwd":str(ROOT),**matrices[label]})
commands.append({"label":"original-red-current","argv":["python","scripts/run_tests_isolated.py","--","python","-m","evidence.r6i-repair.run_original_red"],
    "expandedCommand":"original-red-command.json",**matrices["original-red-current"]})
front_files = ["src/views/AutomationWorkspace.test.tsx","src/views/AutomationView.test.tsx","src/views/CanonicalJobsView.test.tsx",
    "src/services/canonicalJobs.test.ts","src/router.retirement.test.tsx","src/components/automation/fields.test.tsx","src/stores/automationSession.test.tsx"]
commands.extend([
    {"label":"vitest-final-v4","argv":["pnpm","exec","vitest","run",*front_files,"--reporter=verbose"],"cwd":str(ROOT/"packages/web"),"exit":0,"tests":77,"passed":77,"failed":0,"skipped":0},
    {"label":"tsc-final-v5","argv":["pnpm","exec","tsc","-b","--noEmit"],"cwd":str(ROOT/"packages/web"),"exit":0},
    {"label":"build-final-v4","argv":["pnpm","build"],"cwd":str(ROOT/"packages/web"),"exit":0,"precompressedAssets":88},
    {"label":"identity-positive","argv":["python","scripts/run_tests_isolated.py","--","python","scripts/probe_pending_adoption.py","--output","evidence/r6i-repair/identity-positive.json"],"exit":0,"breakpoints":3},
    {"label":"candidate-epoch-final","argv":["python","scripts/run_tests_isolated.py","--","python","-m","evidence.r6i-repair.owner_epoch_probe","evidence/r6i-repair/candidate-epoch-final.json"],"exit":0,"effects":1},
    {"label":"dependencies-final","argv":["python","scripts/check_scheduler_dependencies.py","--output","evidence/r6i-repair/dependencies-final.json"],"exit":0},
    {"label":"baseline-routes","argv":["python","scripts/run_tests_isolated.py","--","python","-m","evidence.r6i-repair.verify_baseline_routes"],"expandedCommand":"baseline-routes.json","exit":0,"tests":27,"passed":27,"skipped":0},
    {"label":"baseline-additional","argv":["python","scripts/run_tests_isolated.py","--","python","-m","evidence.r6i-repair.verify_additional_baseline"],"expandedCommand":"baseline-additional-command.json","exit":1,"tests":2,"failed":2,"skipped":0},
    {"label":"source-commit","argv":["git","commit","-m","fix(jobs): persist canonical attempt epoch and migrate scheduler management"],"exit":0,"hook":"React TypeScript compilation passed"},
])
all_runs = {}
for path in sorted(OUT.glob("*.xml")):
    suite = ET.parse(path).find("testsuite")
    all_runs[path.name] = {key:int(suite.attrib[key]) for key in ("tests","failures","errors","skipped")}
manifest = {"baselineSha":BASE,"repairBaseSha":PREVIOUS,"sourceSha":SOURCE,"branch":git("branch","--show-current"),
    "uniqueFinalPytest": {"tests":len(final),**total},"selectedMatrices":matrices,"allRawXmlRuns":all_runs,
    "finalNodes":[final[key] for key in sorted(final)],"vitestNodes":[{"path":"packages/web/src/"+path,"node":name,"status":"passed"} for path,name in native_nodes],
    "commands":commands,"exactSourcePathsFromIntegrationBase":changed,"repairDiff":repair,
    "unchangedInThisRepair":unchanged,"finishAttemptAstUnchangedFromIntegrationBase":True,
    "retainedHosts":[{"path":path,"gitBlob":git("rev-parse",SOURCE+":"+path)} for path in unchanged if path.endswith(("store.py","time_events.py","legacy_bridge.py"))],
    "unverified":["full backend suite","remote three-platform CI","real provider effects","deployed full Pan/browser acceptance","real profile migration/51 versus 60 inventory","legacy bridge final cursor/receipt/owner terminal acceptance","original red nodes exact successors identified in assessment"],
    "profileBoundary":{"migrated51":"not read or changed","unrecognized60":"not read or changed","activeLegacyZeroClaim":False}}
(OUT / "validation.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
print(json.dumps({"sourceSha":SOURCE,"uniqueFinalPytest":manifest["uniqueFinalPytest"],"vitest":len(native_nodes),"paths":len(changed)}))
