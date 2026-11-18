"""Failure-closed contracts for the CI shard runner and aggregator."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci_test_shards import shard_of, verify


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_test_shards.py"


def _run(root, index, count, manifest):
    return subprocess.run([sys.executable, str(SCRIPT), "run", "--index", str(index),
                           "--count", str(count), "--manifest", str(manifest), "--",
                           str(root), "-q", "-o", "addopts=",
                           f"--junitxml={manifest.with_suffix('.xml')}"],
                          cwd=root, text=True, capture_output=True)


def _write_files(root):
    # Both partitions get real pytest execution, including parametrization and skip.
    names = {}
    for i in range(100):
        filename = f"test_part_{i}.py"
        names.setdefault(shard_of(filename, 2), filename)
        if len(names) == 2:
            break
    for filename in names.values():
        (root / filename).write_text(
            "import pytest\n@pytest.mark.parametrize('x', [1, 2])\n"
            "def test_ok(x): assert x > 0\n"
            "@pytest.mark.skip(reason='existing skip')\n"
            "def test_skip(): pass\n", encoding="utf-8")
    return names


def test_complete_shards_execute_every_original_identity(tmp_path):
    _write_files(tmp_path)
    paths = [tmp_path / f"{i}.json" for i in range(2)]
    for i, path in enumerate(paths):
        result = _run(tmp_path, i, 2, path)
        assert result.returncode == 0, result.stdout + result.stderr
    merged = tmp_path / "merged.xml"
    verify(paths, 2, [p.with_suffix('.xml') for p in paths], merged)
    import xml.etree.ElementTree as ET
    assert len(ET.parse(merged).getroot().findall('.//testcase')) == 6
    with pytest.raises(OSError):
        verify(paths, 2, [paths[0].with_suffix('.xml'), tmp_path / 'missing.xml'], merged)
    malformed = tmp_path / 'malformed.xml'
    malformed.write_text('<not-xml', encoding='utf-8')
    with pytest.raises(ET.ParseError):
        verify(paths, 2, [paths[0].with_suffix('.xml'), malformed], merged)
    tree = ET.parse(paths[1].with_suffix('.xml'))
    tree.getroot().find('.//testcase').append(ET.Element('failure'))
    failed = tmp_path / 'failed.xml'
    tree.write(failed, encoding='utf-8', xml_declaration=True)
    with pytest.raises(ValueError):
        verify(paths, 2, [paths[0].with_suffix('.xml'), failed], merged)
    a, b = (json.loads(p.read_text(encoding="utf-8")) for p in paths)
    assert len(a["collected"]) == len(b["collected"]) == 6
    assert len(a["finished"]) + len(b["finished"]) == 6


def test_missing_failed_unfinished_duplicate_and_collection_mismatch_are_rejected(tmp_path):
    _write_files(tmp_path)
    paths = [tmp_path / f"{i}.json" for i in range(2)]
    for i, path in enumerate(paths):
        assert _run(tmp_path, i, 2, path).returncode == 0
    original = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    with pytest.raises(ValueError):
        verify(paths[:1], 2)
    for mutation in ("exitcode", "unfinished", "duplicate", "collection", "unknown"):
        changed = dict(original[1])
        if mutation == "exitcode": changed["exitcode"] = 1
        elif mutation == "unfinished": changed["finished"] = changed["finished"][:-1]
        elif mutation == "duplicate": changed["index"] = 0
        elif mutation == "collection": changed["collected"] = changed["collected"][:-1]
        else: changed["exitcode"] = None
        paths[1].write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError):
            verify(paths, 2)
    paths[1].write_text(json.dumps(original[1]), encoding="utf-8")
    verify(paths, 2)


def test_real_test_and_collection_errors_remain_failures(tmp_path):
    names = _write_files(tmp_path)
    (tmp_path / names[0]).write_text("def test_failure(): assert False\n", encoding="utf-8")
    paths = [tmp_path / f"{i}.json" for i in range(2)]
    results = [_run(tmp_path, i, 2, paths[i]) for i in range(2)]
    assert any(r.returncode != 0 for r in results)
    with pytest.raises(ValueError): verify(paths, 2)
    (tmp_path / names[0]).write_text("raise RuntimeError('collection failure')\n", encoding="utf-8")
    results = [_run(tmp_path, i, 2, paths[i]) for i in range(2)]
    assert all(r.returncode != 0 for r in results)
    with pytest.raises(ValueError): verify(paths, 2)


def test_collection_skip_is_preserved_once_in_merged_junit(tmp_path):
    import xml.etree.ElementTree as ET
    _write_files(tmp_path)
    (tmp_path / 'test_module_skip.py').write_text(
        "import pytest\npytest.skip('existing module skip', allow_module_level=True)\n",
        encoding='utf-8')
    paths = [tmp_path / f'{i}.json' for i in range(2)]
    for i, path in enumerate(paths):
        result = _run(tmp_path, i, 2, path)
        assert result.returncode == 0, result.stdout + result.stderr
    merged = tmp_path / 'merged.xml'
    verify(paths, 2, [p.with_suffix('.xml') for p in paths], merged)
    cases = ET.parse(merged).getroot().findall('.//testcase')
    assert len(cases) == 7
    assert sum(c.find('skipped') is not None and
               c.find('skipped').get('message') == 'collection skipped' for c in cases) == 1
