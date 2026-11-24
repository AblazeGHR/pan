"""A disposable Zotero smoke must refuse a pre-existing or unknown process."""
import pytest
from tests.support.acp_zotero_safety import require_no_existing_zotero


def test_preflight_refuses_existing_and_unknown_processes():
    class Process:
        def __init__(self, name, pid): self._name, self.pid = name, pid
        def name(self):
            if self._name is None: raise PermissionError('unreadable')
            return self._name
    require_no_existing_zotero([Process('other.exe', 1)])
    require_no_existing_zotero([Process('zotero.exe', 4)], owned_pids={4})
    with pytest.raises(RuntimeError, match='existing Zotero'):
        require_no_existing_zotero([Process('ZOTERO.EXE', 2)])
    with pytest.raises(RuntimeError, match='inventory'):
        require_no_existing_zotero([Process(None, 3)])
