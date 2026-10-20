import asyncio
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from packages.core import workdir_retention as wr
from packages.core import data_retention as dr
from packages.web import server as srv


def old_tree(tmp_path):
    root = tmp_path / 'workdirs'
    path = root / 'orphan'
    (path / 'nested').mkdir(parents=True)
    (path / 'nested' / 'file').write_text('keep')
    old = time.time() - 40 * 86400
    for item in [*path.rglob('*'), path]:
        os.utime(item, (old, old))
    return root, path


def collector(root, state=lambda path: (False, None)):
    return wr.WorkdirRetention(root, state)


def test_recursive_mtime_preview_and_delete(tmp_path):
    root, path = old_tree(tmp_path)
    service = collector(root)
    preview = service.run(30)
    assert preview['candidates'][0]['entries'] == 3
    assert preview['candidates'][0]['path'] == str(path)
    assert path.exists()
    assert service.run(30, ['orphan'], delete=True)['deleted'] == 1
    assert not path.exists()


@pytest.mark.parametrize('entry', ['nested/file', 'nested', '.'])
def test_recent_descendant_or_root_prevents_deletion(tmp_path, entry):
    root, path = old_tree(tmp_path)
    os.utime(path / entry, None)
    assert collector(root).run(30, ['orphan'], delete=True)['deleted'] == 0
    assert (path / 'nested/file').read_text() == 'keep'


@pytest.mark.parametrize('change', ['file', 'reference'])
def test_preview_is_not_delete_authority(tmp_path, change):
    root, path = old_tree(tmp_path)
    referenced = False
    service = collector(root, lambda path: (referenced, 'referenced'))
    assert service.run(30)['candidates']
    if change == 'file':
        (path / 'nested/file').write_text('new')
    else:
        referenced = True
    assert service.run(30, ['orphan'], delete=True)['deleted'] == 0
    assert path.exists()


@pytest.mark.parametrize('name', ['../orphan', '.', '..', '/tmp', 'a/b', 'a\\b', 'C:orphan', ''])
def test_forged_paths_are_never_deleted(tmp_path, name):
    root, path = old_tree(tmp_path)
    assert collector(root).run(30, [name], delete=True)['deleted'] == 0
    assert path.exists()


def test_link_and_scan_error_fail_closed(tmp_path, monkeypatch):
    root, path = old_tree(tmp_path)
    outside = tmp_path / 'outside'
    outside.write_text('safe')
    try:
        (path / 'link').symlink_to(outside)
    except OSError:
        # Windows junctions are also reparse points and need no symlink privilege.
        import subprocess
        (path / 'link').mkdir()
        (path / 'link').rmdir()
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(path / 'link'), str(tmp_path)], check=True, capture_output=True)
    assert not collector(root).run(30)['candidates']
    assert outside.read_text() == 'safe'
    monkeypatch.setattr(wr, 'tree_snapshot', lambda *args: (_ for _ in ()).throw(PermissionError()))
    result = collector(root).run(30, ['orphan'], delete=True)
    assert result['skipReasons'] == {'PermissionError': 1}


@pytest.mark.parametrize('kind', ['session', 'workspace'])
@pytest.mark.parametrize('relation', ['same', 'parent', 'child'])
def test_all_reference_overlaps_protected(tmp_path, monkeypatch, kind, relation):
    root, path = old_tree(tmp_path)
    sessions = tmp_path / 'sessions'
    sessions.mkdir()
    workspaces = tmp_path / 'workspaces'
    workspaces.mkdir()
    monkeypatch.setattr(srv.sess, 'SESSION_DIR', sessions)
    monkeypatch.setattr(srv.sess, '_cache', {})
    monkeypatch.setattr(srv.workspaces, 'WORKSPACE_DIR', workspaces)
    reference = {'same': path, 'parent': root, 'child': path / 'nested'}[relation]
    if kind == 'session':
        (sessions / 'archived.json').write_text(json.dumps({'id': 'archived', 'archived': True, 'workdir': str(reference)}))
    else:
        (workspaces / 'ws.json').write_text(json.dumps({'dirs': [str(reference)]}))
    assert srv._retention_workdir_reference_state('', path)[0] is True


def test_corrupt_reference_and_limits_fail_closed(tmp_path, monkeypatch):
    root, path = old_tree(tmp_path)
    sessions = tmp_path / 'sessions'
    sessions.mkdir()
    (sessions / 'broken.json').write_text('{')
    monkeypatch.setattr(srv.sess, 'SESSION_DIR', sessions)
    assert srv._retention_workdir_reference_state('', path)[0] is None
    monkeypatch.setattr(wr, 'MAX_ENTRIES', 1)
    result = collector(root).run(30)
    assert not result['candidates'] and result['skipReasons']['scan_limit'] == 1


def test_reference_commit_serializes_and_rejects_deleted_path(tmp_path):
    root, path = old_tree(tmp_path)
    entered, finished = threading.Event(), threading.Event()
    errors = []
    @wr.reference_commit
    def save(owner):
        finished.set()
    def writer():
        entered.set()
        try:
            save(SimpleNamespace(workdir=str(path)))
        except ValueError as exc:
            errors.append(str(exc))
    with wr.REFERENCE_LOCK:
        thread = threading.Thread(target=writer)
        thread.start()
        assert entered.wait(1)
        assert not finished.is_set()
        assert collector(root).run(30, ['orphan'], delete=True)['deleted'] == 1
    thread.join(2)
    assert not thread.is_alive() and errors and not finished.is_set()


def test_auto_off_old_config_and_enabled_uses_existing_runner(tmp_path):
    root, path = old_tree(tmp_path)
    policy = {}
    service = dr.DataRetentionService(sessions_root=tmp_path/'sessions',
        attachments_root=tmp_path/'attachments', qq_history_root=tmp_path/'qq',
        qq_media_root=tmp_path/'media', status_path=tmp_path/'data/retention/status.json',
        policy_loader=lambda: policy, session_delete=lambda *a: {},
        workdir_scan=lambda days: collector(root).run(days, delete=True))
    service.run_once(force=True)
    assert path.exists() and not dr.normalize_policies({})['workdirs']['enabled']
    policy['workdirs'] = {'enabled': True, 'days': 30}
    assert service.run_once(force=True)['workdirs']['deleted'] == 1
    assert not path.exists()


def test_reference_commit_that_wins_is_protected(tmp_path):
    root, path = old_tree(tmp_path)
    referenced = []
    @wr.reference_commit
    def save(owner):
        referenced.append(owner.workdir)
    save(SimpleNamespace(workdir=str(path)))
    result = collector(root, lambda p: (str(p) in referenced, 'referenced')).run(30, ['orphan'], delete=True)
    assert result['deleted'] == 0 and path.exists()


def test_session_and_workspace_mutations_reject_retired_reference(tmp_path, monkeypatch):
    root, path = old_tree(tmp_path)
    monkeypatch.setattr(srv.sess, 'SESSION_DIR', tmp_path/'sessions')
    monkeypatch.setattr(srv.sess, '_cache', {})
    monkeypatch.setattr(srv.workspaces, 'WORKSPACE_DIR', tmp_path/'workspaces')
    monkeypatch.setattr(srv.workspaces, '_cache', {})
    session = srv.sess.create('safe', workdir=str(tmp_path))
    workspace = srv.workspaces.create('safe')
    assert collector(root).run(30, ['orphan'], delete=True)['deleted'] == 1
    with pytest.raises(ValueError):
        session.workdir = str(path)
    assert session.workdir == str(tmp_path)
    with pytest.raises(ValueError):
        srv.workspaces.update(workspace, dirs=[str(path)])
    assert workspace.dirs == []
    with pytest.raises(ValueError):
        srv.sess.create('late', workdir=str(path))


def test_change_between_final_checks_fails_closed(tmp_path):
    root, path = old_tree(tmp_path)
    calls = []
    def state(p):
        calls.append(p)
        if len(calls) == 1:
            (path/'nested/file').write_text('changed')
        return False, None
    assert collector(root, state).run(30, ['orphan'], delete=True)['deleted'] == 0
    assert (path/'nested/file').read_text() == 'changed'


def test_directory_pins_prevent_windows_rename(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows directory share-delete semantics')
    root, path = old_tree(tmp_path)
    snapshot = wr.tree_snapshot(path, time.monotonic()+10, [100])
    with wr.pinned_directories(root, path, snapshot):
        with pytest.raises(OSError):
            path.rename(root/'moved')
        with pytest.raises(OSError):
            (path/'nested').rename(path/'moved')
    assert collector(root).run(30, ['orphan'], delete=True)['deleted'] == 1


def test_partial_failure_is_observable_and_other_items_continue(tmp_path, monkeypatch):
    root, path = old_tree(tmp_path)
    second = root/'second'
    second.mkdir()
    old = time.time()-40*86400
    os.utime(second, (old, old))
    original = Path.unlink
    def fail(item, *args, **kwargs):
        if item.name == 'file':
            raise PermissionError('blocked')
        return original(item, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail)
    result = collector(root).run(30, ['orphan', 'second'], delete=True)
    assert result['deleted'] == 1 and result['results'][0]['reason'] == 'PermissionError'
    assert path.exists() and not second.exists()


def test_persisted_policy_includes_workdirs_and_rejects_invalid_n(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(srv, 'load_config', lambda: {'data_retention': {'sessions': {'enabled': False, 'days': None}}})
    monkeypatch.setattr(srv, 'read_config_file', lambda: {})
    monkeypatch.setattr(srv, 'save_config', saved.append)
    monkeypatch.setattr(srv, '_get_data_retention_service', lambda: SimpleNamespace(get_status=lambda: {}))
    response = asyncio.run(srv.api_put_data_retention({'policies': {'workdirs': {'enabled': True, 'days': 15}}}))
    assert saved[-1]['data_retention']['workdirs'] == {'enabled': True, 'days': 15}
    assert response['policies']['sessions']['enabled'] is False
    with pytest.raises(srv.HTTPException):
        asyncio.run(srv.api_put_data_retention({'policies': {'workdirs': {'enabled': True, 'days': 0}}}))


def test_api_preview_and_delete_run_off_eventloop(tmp_path, monkeypatch):
    root, path = old_tree(tmp_path)
    threads = []
    main = threading.get_ident()
    def state(candidate):
        threads.append(threading.get_ident())
        return False, None
    monkeypatch.setattr(srv, '_workdir_collector', lambda: collector(root, state))
    preview = asyncio.run(srv.api_preview_workdir_retention({'days': 30}))
    assert preview['candidates'][0]['name'] == 'orphan'
    response = asyncio.run(srv.api_delete_workdir_retention({'days': 30, 'names': ['orphan']}))
    assert response['deleted'] == 1 and not path.exists()
    assert threads and all(thread != main for thread in threads)


@pytest.mark.parametrize('days', [0, -1, True, 1.2, None, 36501])
def test_api_validates_days_and_rejects_paths(tmp_path, monkeypatch, days):
    root, path = old_tree(tmp_path)
    monkeypatch.setattr(srv, '_workdir_collector', lambda: collector(root))
    with pytest.raises(srv.HTTPException):
        asyncio.run(srv.api_preview_workdir_retention({'days': days}))
    with pytest.raises(srv.HTTPException):
        asyncio.run(srv.api_delete_workdir_retention({'days': 30, 'path': str(path)}))
    assert path.exists()
