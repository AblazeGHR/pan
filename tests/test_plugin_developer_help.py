"""Developer helper validation must never execute registered providers."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plugin_developer_validator', ROOT / 'tools/validate_plugin.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def test_static_examples_and_default_catalog_without_subprocess(monkeypatch):
    import subprocess
    launch = Mock(side_effect=AssertionError('No provider execution allowed'))
    monkeypatch.setattr(subprocess, 'Popen', launch)
    result = validator.inspect(['docs/examples/plugin-capabilities/manifest.json'])
    assert result['ok'] and result['plugins'][0]['id'] == 'example'
    default = validator.inspect(['manifest.json', 'packages/mcp/manifest.json'])
    bridge = next(p for p in default['plugins'] if p['id'] == 'cc-switch-bridge')
    assert bridge['panels'] == ['accounts']
    assert len(bridge['agentCapabilities']) == 4
    launch.assert_not_called()


def test_missing_and_invalid_manifest_fail_with_sanitized_cli_output(tmp_path, capsys):
    with pytest.raises(ValueError, match='not found'):
        validator.inspect([str(tmp_path / 'absent.json')])
    path = tmp_path / 'bad.json'
    path.write_text('{"plugins": [{"protocol":"secret-fixture-value"}]}', encoding='utf-8')
    assert validator.main([str(path), '--json']) == 1
    output = capsys.readouterr().out
    assert '"ok": false' in output and 'secret-fixture-value' not in output
