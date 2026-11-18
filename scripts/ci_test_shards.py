"""Run and verify file-partitioned pytest jobs without losing collection failures."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def shard_of(nodeid: str, count: int) -> int:
    # Keep all tests from one file together and preserve their collection order.
    filename = nodeid.split("::", 1)[0]
    return int.from_bytes(hashlib.sha256(filename.encode("utf-8")).digest()[:8], "big") % count


class Recorder:
    def __init__(self, index: int, count: int):
        self.index, self.count = index, count
        self.collected: list[str] | None = None
        self.selected: list[str] = []
        self.finished: list[str] = []
        self.collection_errors: list[str] = []
        self.collection_skips: list[str] = []

    def pytest_collection_modifyitems(self, session, config, items):
        self.collected = [item.nodeid for item in items]
        selected, deselected = [], []
        for item in items:
            (selected if shard_of(item.nodeid, self.count) == self.index else deselected).append(item)
        self.selected = [item.nodeid for item in selected]
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(report.nodeid)
        elif report.skipped:
            self.collection_skips.append(report.nodeid)

    def pytest_runtest_logfinish(self, nodeid, location):
        self.finished.append(nodeid)


def run(index: int, count: int, out: Path, pytest_args: list[str]) -> int:
    import pytest
    if count < 1 or not 0 <= index < count or not pytest_args:
        raise ValueError("invalid shard configuration or empty pytest arguments")
    recorder = Recorder(index, count)
    exitcode = int(pytest.main(pytest_args, plugins=[recorder]))
    payload = {"version": 1, "index": index, "count": count, "exitcode": exitcode,
               "collected": recorder.collected, "selected": recorder.selected,
               "finished": recorder.finished, "collection_errors": recorder.collection_errors,
               "collection_skips": recorder.collection_skips}
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(out.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    os.replace(temp, out)
    return exitcode


def verify(paths: list[Path], count: int, junit_paths: list[Path] | None = None,
           merged_junit: Path | None = None) -> None:
    if count < 1 or len(paths) != count:
        raise ValueError("missing shard manifest")
    seen_indexes: set[int] = set()
    reference: Counter[str] | None = None
    reference_skips: Counter[str] | None = None
    union: Counter[str] = Counter()
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        index = data.get("index")
        if (data.get("version") != 1 or type(index) is not int or not 0 <= index < count
                or index in seen_indexes or data.get("count") != count
                or type(data.get("exitcode")) is not int or data["exitcode"] != 0
                or data.get("collection_errors") != []):
            raise ValueError(f"failed, duplicate or malformed shard: {path}")
        seen_indexes.add(index)
        for key in ("collected", "selected", "finished", "collection_skips"):
            if not isinstance(data.get(key), list) or not all(isinstance(x, str) for x in data[key]):
                raise ValueError(f"missing {key}: {path}")
        collected, selected, finished = (Counter(data[k]) for k in ("collected", "selected", "finished"))
        skips = Counter(data["collection_skips"])
        if not collected or not selected or selected != finished:
            raise ValueError(f"empty, unfinished or mismatched execution: {path}")
        expected = Counter(x for x in data["collected"] if shard_of(x, count) == index)
        if (selected != expected or (reference is not None and collected != reference)
                or (reference_skips is not None and skips != reference_skips)):
            raise ValueError(f"selection or full collection mismatch: {path}")
        reference = collected
        reference_skips = skips
        union.update(selected)
    if seen_indexes != set(range(count)) or union != reference:
        raise ValueError("shard union differs from full collection")
    if junit_paths is not None:
        if len(junit_paths) != count or merged_junit is None:
            raise ValueError("missing JUnit files or merge destination")
        combined = ET.Element("testsuites", name="pytest tests")
        skip_identities = None
        for shard_number, (path, manifest) in enumerate(zip(junit_paths, paths)):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            root = ET.parse(path).getroot()
            suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
            cases = [case for suite in suites for case in suite.iter("testcase")]
            collection_cases = [case for case in cases if case.find("skipped") is not None
                                and case.find("skipped").get("message") == "collection skipped"]
            identities = Counter((case.get("classname"), case.get("name")) for case in collection_cases)
            if (not suites or len(cases) != len(data["finished"]) + len(data["collection_skips"])
                    or len(collection_cases) != len(data["collection_skips"])
                    or (skip_identities is not None and identities != skip_identities)
                    or any(case.find("failure") is not None or case.find("error") is not None
                           for case in cases)):
                raise ValueError(f"missing or failed JUnit cases: {path}")
            skip_identities = identities
            if shard_number:
                for suite in suites:
                    duplicates = [case for case in suite.findall("testcase") if case in collection_cases]
                    for case in duplicates:
                        suite.remove(case)
                    if duplicates:
                        for key in ("tests", "skipped"):
                            suite.set(key, str(int(suite.get(key, "0")) - len(duplicates)))
            combined.extend(suites)
        merged_junit.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(combined).write(merged_junit, encoding="utf-8", xml_declaration=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--index", type=int, required=True)
    runner.add_argument("--count", type=int, required=True)
    runner.add_argument("--manifest", type=Path, required=True)
    runner.add_argument("pytest_args", nargs=argparse.REMAINDER)
    check = sub.add_parser("verify")
    check.add_argument("--count", type=int, required=True)
    check.add_argument("--junit", type=Path, action="append")
    check.add_argument("--merged-junit", type=Path)
    check.add_argument("manifests", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        if args.mode == "run":
            pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
            return run(args.index, args.count, args.manifest, pytest_args)
        verify(args.manifests, args.count, args.junit, args.merged_junit)
        print("Shard identities and terminal results verified.")
        return 0
    except (ValueError, OSError, json.JSONDecodeError, ET.ParseError) as exc:
        print(f"Shard verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
