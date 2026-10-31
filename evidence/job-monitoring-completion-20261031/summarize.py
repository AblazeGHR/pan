"""Offline source/evidence binding; never imports product or opens a profile."""
from pathlib import Path
import ast
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "4cf100f38051be48ab9e3b81155bb4ed0be00950"
SOURCE = "1e375d59abc9e163339de6641d51f1a4fee350aa"
FILES = ["packages/jobs/final_service.py", "packages/jobs/independent_executor.py",
         "packages/jobs/independent_execution_runner.py", "tests/test_job_monitoring_completion.py",
         "tests/test_job_automatic_recovery.py", "tests/test_job_recovery_stable_reset.py"]
BOUNDARIES = ["packages/jobs/final_runtime", "packages/core/platform", "packages/core/worker.py",
              "packages/mcp", "scripts/job_final_migration", "packages/jobs/final_contracts.py",
              "packages/jobs/schemas/final", "packages/jobs/process_logs.py", "packages/actions/typed_executor.py",
              "tests/test_background_job_retry_if.py", "tests/test_job_completion_recovery.py",
              "tests/test_authorization_runtime.py", "tests/automation_retirement.json"]
COMMAND_PREFIX = "D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest "
GROUPS = {
    "source-1e375d59-recovery.xml": "tests/test_job_monitoring_completion.py tests/test_job_recovery_stable_reset.py tests/test_job_automatic_recovery.py",
    "source-1e375d59-host-contract.xml": "tests/test_job_final_independent_execution.py tests/test_job_final_executors.py tests/test_automation_retry_parallel.py tests/test_job_final_http_service.py tests/test_job_final_contracts.py",
    "source-1e375d59-logs.xml": "tests/test_job_process_logs.py::test_default_real_large_unicode_combined_log_cold_reopen_and_archive tests/test_job_process_logs.py::test_failed_and_cancelled_real_output_survives_and_no_rerun",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    changed = git("diff", "--name-only", BASE, SOURCE).decode().splitlines()
    assert set(changed) == set(FILES), changed
    assert not git("diff", BASE, SOURCE, "--", *BOUNDARIES)
    sources = {}
    for name in FILES:
        data = git("show", SOURCE + ":" + name)
        assert data.replace(b"\r\n", b"\n") == (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        tree = ast.parse(data)
        symbols = {n.name: n.lineno for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        sources[name] = {"gitBlob":git("rev-parse", SOURCE+":"+name).decode().strip(),
                         "sha256GitBytes":hashlib.sha256(data).hexdigest(), "symbols":symbols}
    exploratory = {}
    for path in OUT.glob('*.xml'):
        tree = ET.parse(path)
        for suite in tree.iter('testsuite'):
            suite.set('hostname','owned-isolated-test-host')
        for failure in tree.iter('failure'):
            if failure.text:
                failure.text = '\n'.join(line.rstrip() for line in failure.text.split('\n'))
        tree.write(path,encoding='utf-8',xml_declaration=True)
        if path.name not in GROUPS:
            cases = list(tree.iter('testcase'))
            exploratory[path.name] = {'collected':len(cases),
                'failuresOrErrors':[c.attrib for c in cases if c.find('failure') is not None or c.find('error') is not None],
                'skipped':[c.attrib for c in cases if c.find('skipped') is not None],
                'completeRun':path.name!='user-interrupted.xml',
                'countedInFinalSource':False}
    results, unique, new_cases = {}, set(), set()
    for name in GROUPS:
        path = OUT / name
        tree = ET.parse(path)
        for suite in tree.iter("testsuite"):
            suite.set("hostname", "owned-isolated-test-host")
        tree.write(path, encoding="utf-8", xml_declaration=True)
        cases = list(tree.iter("testcase"))
        failures = [c.attrib for c in cases if c.find("failure") is not None or c.find("error") is not None]
        skipped = [c.attrib for c in cases if c.find("skipped") is not None]
        assert not failures and not skipped, (name, failures, skipped)
        nodes = {(c.get("classname"), c.get("name")) for c in cases}
        assert not unique.intersection(nodes)
        unique.update(nodes)
        new_cases.update(node for node in nodes if node[0] == "tests.test_job_monitoring_completion")
        results[name] = {"passed":len(cases),"failed":0,"errors":0,"skipped":0,"exitCode":0,
                         "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                         "command":COMMAND_PREFIX+GROUPS[name]+" -q --tb=short --junitxml=evidence/job-monitoring-completion-20261031/"+name}
    proof = json.loads((OUT/"default-detached.json").read_text())
    assert (proof['invocations'],proof['maxAttempts'],proof['attemptGenerations'],proof['distinctEffectKeys']) == (3,2,[1,2,3],1)
    assert proof['coldReopen'] and proof['stableWindowBaseGeneration']==1
    assert ('tests.test_job_monitoring_completion','test_owned_http_detached_monitoring_retry_cold_reopen_and_actual_pulse') in new_cases
    proof['ownedPortReleased'] = True
    proof['portReleaseEvidence'] = 'terminal passing assertion in owned HTTP integration test'
    result = {"baseline":BASE,"productSourceSHA":SOURCE,"files":sources,"unchangedBoundaries":BOUNDARIES,
              "results":results,"uniquePassed":len(unique),"newMonitoringCases":len(new_cases),
              "exploratoryEvidence":exploratory,
              "defaultDetachedProof":proof,"schemaCheckExit":0,"diffCheckExit":0,
              "schemaCheckCommand":"D:/project/Pan-main/.venv/Scripts/python.exe -m packages.jobs.final_contracts --check",
              "unverified":["Linux/macOS runtime","remote CI","full suite","browser/UI","external provider",
                            "real-data migration","deployment","human acceptance","actual detached 24h timeout"]}
    probes = [exploratory[name]['failuresOrErrors'] for name in
              ('source-1e375d59-qq-probe.xml','baseline-4cf100f3-qq-probe.xml')]
    assert all(len(p)==1 for p in probes) and probes[0][0]['name']==probes[1][0]['name']
    for name in ('source-1e375d59-qq-probe.xml','baseline-4cf100f3-qq-probe.xml'):
        failure = next(ET.parse(OUT/name).iter('failure')).text
        assert ':91:' in failure and 'uncertain' in failure and 'completed' in failure
    result['baselineEquivalentFailure'] = {'test':probes[0][0]['name'], 'line':91,
        'candidateExit':1,'baselineModuleOverlayExit':1,
        'comparison':'fresh interpreter with exact three changed product modules loaded from 4cf100f3 before pytest collection',
        'baselineCommand':COMMAND_PREFIX.split(' -m pytest ')[0]+' evidence/job-monitoring-completion-20261031/baseline_qq_probe.py "tests/test_retirement_rev10_qq_retry.py::'+probes[0][0]['name']+'" -q --tb=short --junitxml=evidence/job-monitoring-completion-20261031/baseline-4cf100f3-qq-probe.xml'}
    (OUT/"verification.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"sourceSHA":SOURCE,"uniquePassed":len(unique),"newMonitoringCases":len(new_cases)}))


if __name__ == "__main__":
    main()
