"""Validate CI contract without creating or contacting any Pan service."""
import subprocess
import sys
from pathlib import Path

from scripts import minimal_release_ci as gate


def test_supplemental_uses_isolated_wrapper(tmp_path):
    argv = gate.command('supplemental', tmp_path)
    assert argv[:4] == [sys.executable, str(gate.ROOT / 'scripts/run_tests_isolated.py'), '--', sys.executable]
    assert 'tests/test_terminal_secret_store.py' in argv
    assert not any(x.startswith(('--ignore', '--deselect')) for x in argv)


def test_missing_extracted_contract_is_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'SUPPLEMENTAL', ('tests/definitely_missing_minimal_contract.py',))
    child = subprocess.run(gate.command('supplemental', tmp_path), cwd=gate.ROOT,
                           capture_output=True, text=True)
    assert child.returncode != 0
    assert 'definitely_missing_minimal_contract.py' in child.stdout + child.stderr


def test_missing_required_function_is_reported(tmp_path, monkeypatch):
    filename = 'contract.py'
    (tmp_path / filename).write_text('def existing(): pass\n', encoding='utf-8')
    monkeypatch.setattr(gate, 'REQUIRED', {filename: ('existing', 'required')})
    assert gate.missing_contracts(tmp_path) == [filename + '::required']


def test_fast_manifest_must_include_extracted_contract_files(tmp_path, monkeypatch):
    script = tmp_path / 'scripts/validate_minimal_platform.py'
    script.parent.mkdir()
    script.write_text("FAST = ['included.py']\n", encoding='utf-8')
    monkeypatch.setattr(gate, 'REQUIRED', {'included.py': ('test_one',), 'omitted.py': ('test_two',)})
    assert gate.missing_fast_coverage(tmp_path) == ['FAST omits required contract file: omitted.py']
