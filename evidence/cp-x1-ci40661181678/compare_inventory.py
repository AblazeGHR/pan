"""Exact per-version remote comparison; removed failures require XML status proof."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", type=Path, required=True)
    args = parser.parse_args()
    path = args.remote / "failure-inventory.json"
    current = json.loads(path.read_text(encoding="utf-8"))
    previous = json.loads((ROOT / "evidence/cp-x1-remote-repair-20261028/windows-node-classification.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location("old_classifier", ROOT / "evidence/cp-x1-remote-repair-20261028/classify_remote.py")
    classifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(classifier)
    result = []
    for version, expected in (("3.12", 241), ("3.14", 240)):
        artifact = f"artifacts\\pytest-results-{version}\\pytest-results.xml"
        rows = [r for r in current if r["artifact"] == artifact]
        assert len(rows) == expected
        old = next(j for j in previous["jobs"] if j["artifact"] == artifact)
        key = lambda r: (r["node"], r["kind"])
        old_by_key = {key(r): r for r in old["records"]}
        xml = args.remote.joinpath(*artifact.split("\\"))
        tree = ET.parse(xml)
        cases = {}
        for case in tree.getroot().iter("testcase"):
            status = "failed" if case.find("failure") is not None else "error" if case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
            cases[case.attrib["classname"] + "::" + case.attrib["name"]] = status
        assert sum(len(c.findall("failure")) + len(c.findall("error")) for c in tree.getroot().iter("testcase")) == expected
        records = []
        for index, r in enumerate(rows):
            prior = old_by_key.get(key(r))
            if r["node"].startswith("tests.test_p7a_successors::"):
                category, group, owner = "delegated-card-regression", "approved-canonical-graph-unavailable", "Original Card TA; no p7a/Card edits in CP-X1"
            elif r["node"].startswith("tests.test_terminal_guard::"):
                category, group, owner = "remote-only-unresolved", "holder-report-not-created", "Terminal platform owner if reproducible; no proof kill-on-close ran"
            else:
                category, group, _, owner = classifier.classify({**r, "text": r["trace"]})
                if r["node"].endswith("test_quota_policy_owned_http_default_unavailable_is_unknown_real_http"):
                    group = "legacy-job-denial-envelope-after-typo-fix"
            records.append({**r, "recordIndex": index, "category": category, "group": group,
                            "smallestSymbolProposal": owner,
                            "traceSha256": digest(r["trace"]),
                            "previous": None if prior is None else {
                                "category": prior["category"], "group": prior["group"],
                                "traceSha256": digest(prior["text"]),
                                "sameRawTrace": prior["text"] == r["trace"],
                                "sameMessage": prior["message"] == r["message"],
                                "successorMapping": prior["existingSuccessorMapping"]},
                            "localFixClaim": "fixture candidate only" if r["node"].startswith((
                                "tests.test_canonical_form_metadata::test_action_catalog_reference",
                                "tests.test_job_retirement_closure_successors::test_target_and_text_edit")) else "none"})
        after = {key(r) for r in rows}
        removed = [{"node": r["node"], "kind": r["kind"], "currentXmlStatus": cases.get(r["node"], "absent")}
                   for r in old["records"] if key(r) not in after]
        cohort = [{"node": r["node"], "kind": r["kind"], "priorGroup": r["group"],
                   "currentXmlStatus": cases.get(r["node"], "absent")}
                  for r in old["records"] if r["category"] == "new-regression-candidate"]
        assert len(cohort) == 38
        result.append({"version": version, "xmlSha256": hashlib.sha256(xml.read_bytes()).hexdigest(),
                       "suite": tree.getroot()[0].attrib, "failureErrorRecords": expected,
                       "categories": dict(Counter(r["category"] for r in records)),
                       "groups": dict(Counter(r["group"] for r in records)),
                       "removedFailureRecords": removed,
                       "addedFailureRecords": [r["node"] for r in rows if key(r) not in old_by_key],
                       "prior38Cohort": cohort, "records": records})
    report = {"runId": 40661181678, "headSha": "87c444ce896e768a75bc74fde1af834952f4875c",
              "previousRun": 40637381532, "sourceInventorySha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "categoryBoundary": "retired is not migration acceptance; unresolved is not proven new introduction; all target results are actual remote XML",
              "jobs": result}
    if (OUT / "validation.json").is_file():
        validation = json.loads((OUT / "validation.json").read_text(encoding="utf-8"))
        local = ET.parse(OUT / "fixture-regression.xml")
        passed = {c.attrib["classname"] + "::" + c.attrib["name"] for c in local.getroot().iter("testcase")
                  if c.find("failure") is None and c.find("error") is None and c.find("skipped") is None}
        for job in result:
            for record in job["records"]:
                if record["localFixClaim"] == "fixture candidate only":
                    assert record["node"] in passed
                    record["localDisposition"] = {
                        "codeSha": validation["codeSha"], "originalNodePassedLocally": True,
                        "evidence": "fixture-regression.xml", "remoteRetested": False,
                        "assertionsUnchanged": validation["allOriginalTestFunctionsAstEqual"]}
    (OUT / "remote-node-comparison.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in result:
        print(r["version"], r["failureErrorRecords"], r["categories"],
              "prior38", dict(Counter(x["currentXmlStatus"] for x in r["prior38Cohort"])))


if __name__ == "__main__":
    main()
