"""Owned native create-edge proof across base and real venv interpreter shapes."""
import json
import os
from pathlib import Path
import site
import subprocess
import sys
import venv

import pytest

from packages.core.terminal.identity import ProcessStatus, close_handle_checked, read_creation_filetime, wait_state
from tests.test_job_lifecycle_prevention import desktop_gone, kill, retain, until


@pytest.mark.skipif(os.name != 'nt', reason='real Windows Job and desktop creation')
@pytest.mark.parametrize('mode', ['base', 'venv-outer', 'venv-inner'])
@pytest.mark.parametrize('phase', ['pre-import', 'post-create'])
def test_owned_creation_edge_preserves_no_child_and_cleanup(tmp_path, monkeypatch, mode, phase):
    runtime = Path(sys._base_executable)
    if mode != 'base':
        envdir = tmp_path / 'owned-venv'
        venv.EnvBuilder(with_pip=False, system_site_packages=True).create(envdir)
        runtime = envdir / 'Scripts/python.exe'
        assert runtime.is_file() and (envdir / 'pyvenv.cfg').is_file()
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(Path(__file__).resolve().parents[1]), *site.getsitepackages()]))
    helper = Path(__file__).parent / 'support/cpx1_creation_edge_runner.py'
    runner = subprocess.Popen([str(runtime), str(helper), str(tmp_path), phase],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    inner = child = None
    desktop = None
    try:
        outer_birth = read_creation_filetime(int(runner._handle))
        assert outer_birth and until(lambda: (tmp_path / 'runner.json').exists(), timeout=15)
        actual = json.loads((tmp_path / 'runner.json').read_text(encoding='utf-8'))
        inner = retain(actual['pid'], actual['birth'])
        if mode == 'base':
            assert actual['pid'] == runner.pid
        else:
            assert Path(actual['prefix']).resolve() == envdir.resolve()
            assert actual['prefix'] != actual['basePrefix']
            assert actual['pid'] != runner.pid, 'named-venv redirector/inner boundary unavailable'
        if phase == 'post-create':
            assert until(lambda: (tmp_path / 'suspended.json').exists(), timeout=15)
            fact = json.loads((tmp_path / 'suspended.json').read_text(encoding='utf-8'))
            child = retain(fact['pid'], fact['birth'])
            desktop = fact['desktop']
            assert desktop.startswith('PanJob_') and not desktop_gone(desktop)
        assert not (tmp_path / 'must-not-execute').exists()
        kill(inner if mode == 'venv-inner' else int(runner._handle))
        runner.wait(timeout=10)
        assert until(lambda: wait_state(inner) is ProcessStatus.DEAD), 'original owned inner runner survived'
        if child:
            assert until(lambda: wait_state(child) is ProcessStatus.DEAD), 'owned suspended child survived'
            assert until(lambda: desktop_gone(desktop)), 'original private desktop survived'
        assert not (tmp_path / 'must-not-execute').exists()
        target = Path(os.environ.get('CPX1_LIFECYCLE_EVIDENCE_ROOT', str(tmp_path))) / 'creation-edges'
        target.mkdir(parents=True, exist_ok=True)
        (target / f'{mode}-{phase}.json').write_text(json.dumps({'mode': mode, 'phase': phase,
            'outerPid': runner.pid, 'outerBirth': str(outer_birth), 'innerPid': actual['pid'],
            'innerBirth': str(actual['birth']), 'childDead': child is None or wait_state(child) is ProcessStatus.DEAD,
            'innerDead': wait_state(inner) is ProcessStatus.DEAD, 'desktopGone': desktop is None or desktop_gone(desktop),
            'markerExecuted': (tmp_path / 'must-not-execute').exists()}, indent=2), encoding='utf-8')
    finally:
        if runner.poll() is None:
            kill(int(runner._handle))
            runner.wait(timeout=10)
        if inner:
            if wait_state(inner) is not ProcessStatus.DEAD:
                kill(inner)
            assert until(lambda: wait_state(inner) is ProcessStatus.DEAD)
        if child:
            if wait_state(child) is not ProcessStatus.DEAD:
                kill(child)
            assert until(lambda: wait_state(child) is ProcessStatus.DEAD)
        for handle in (child, inner):
            if handle:
                assert close_handle_checked(handle)
