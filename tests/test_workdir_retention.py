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


@pytest.fixture(autouse=True)
def isolated_coordinator(monkeypatch):
    from collections import deque
    monkeypatch.setattr(wr, '_claims', set())
    monkeypatch.setattr(wr, '_completed', deque(maxlen=256))
    monkeypatch.setattr(wr, '_writers', 0)
    monkeypatch.setattr(wr, '_epoch', 0)
    monkeypatch.setattr(wr, '_roots', set())


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


def configure_store(tmp_path, root, monkeypatch):
    sessions = tmp_path/'sessions'
    sessions.mkdir()
    monkeypatch.setattr(srv.sess, 'SESSION_DIR', sessions)
    monkeypatch.setattr(srv.sess, '_cache', {})
    monkeypatch.setattr(srv.workspaces, 'WORKSPACE_DIR', tmp_path/'workspaces')
    monkeypatch.setattr(srv.workspaces, '_cache', {})
    monkeypatch.setattr(srv, 'WORKDIRS_DIR', root)
    unrelated = tmp_path/'unrelated'
    unrelated.mkdir()
    return srv.sess.create('unrelated', workdir=str(unrelated)), unrelated


@pytest.mark.parametrize('phase', ['tree', 'references', 'delete'])
def test_slow_collection_never_blocks_unrelated_eventloop_operations(tmp_path, monkeypatch, phase):
    root, path = old_tree(tmp_path)
    session, unrelated = configure_store(tmp_path, root, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    collector_instance = srv._workdir_collector()
    if phase == 'tree':
        original = wr.tree_snapshot
        def slow(*args):
            if not entered.is_set():
                entered.set()
                assert release.wait(3)
            return original(*args)
        monkeypatch.setattr(wr, 'tree_snapshot', slow)
    elif phase == 'references':
        original = collector_instance.snapshot_loader
        def slow(*args):
            if not entered.is_set():
                entered.set()
                assert release.wait(3)
            return original(*args)
        collector_instance.snapshot_loader = slow
    else:
        original = collector_instance._remove_tree
        def slow(*args):
            entered.set()
            assert release.wait(3)
            return original(*args)
        monkeypatch.setattr(collector_instance, '_remove_tree', slow)

    async def scenario():
        task = asyncio.create_task(asyncio.to_thread(collector_instance.run, 30, ['orphan'], delete=True))
        assert await asyncio.to_thread(entered.wait, 2)
        ticks = []
        async def heartbeat():
            for _ in range(5):
                ticks.append(time.monotonic())
                await asyncio.sleep(.01)
        try:
            started = time.monotonic()
            beat = asyncio.create_task(heartbeat())
            session.workdir = str(unrelated)
            srv.sess.save(session)  # actual synchronous event-loop caller
            await beat
            assert time.monotonic() - started < .5
            assert len(ticks) == 5 and not task.done()
            if phase == 'delete':
                with pytest.raises(ValueError, match='being cleaned'):
                    session.workdir = str(path)
                with pytest.raises(ValueError, match='being cleaned'):
                    srv.sess.create('late', workdir=str(path))
            else:
                # Reference wins before the claim; it must survive collection.
                session.workdir = str(path)
                srv.sess.save(session)
        finally:
            release.set()
        outcome = await asyncio.wait_for(task, 2)
        if phase == 'delete':
            assert outcome['deleted'] == 1 and not path.exists()
            assert session.workdir == str(unrelated)
        else:
            assert outcome['deleted'] == 0 and path.exists()
            assert session.workdir == str(path)
    asyncio.run(scenario())


def test_slow_session_writer_does_not_hold_coordinator_or_invert_store_locks(tmp_path, monkeypatch):
    root, path = old_tree(tmp_path)
    session, unrelated = configure_store(tmp_path, root, monkeypatch)
    other = srv.sess.create('other', workdir=str(unrelated))
    entered, release = threading.Event(), threading.Event()
    original = srv.sess._replace_session_metadata
    def slow(source, target):
        if target.stem == session.id:
            entered.set()
            assert release.wait(3)
        return original(source, target)
    monkeypatch.setattr(srv.sess, '_replace_session_metadata', slow)
    async def scenario():
        session.name = 'slow'
        writer = asyncio.create_task(srv.sess.save_async(session))
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            started = time.monotonic()
            other.workdir = str(unrelated)
            srv.sess.save(other)
            # Global name/index operation takes STORE_LOCK and reserves a ticket.
            srv.sess.create_with_available_name('third', workdir=str(unrelated))
            result = await asyncio.to_thread(srv._workdir_collector().run, 30, ['orphan'], delete=True)
            assert result['deleted'] == 0 and result['error'] == 'reference_transaction_in_progress'
            await asyncio.sleep(.01)
            assert time.monotonic() - started < .5 and path.exists()
        finally:
            release.set()
        # Prove writer completion needs no STORE_LOCK after ticket admission.
        with srv.sess._STORE_LOCK:
            await asyncio.wait_for(writer, 2)
    asyncio.run(scenario())


def test_reference_snapshot_loaded_once_for_preview_then_once_for_final_check(tmp_path):
    root, path = old_tree(tmp_path)
    second = root/'second'
    second.mkdir()
    old = time.time()-40*86400
    os.utime(second, (old, old))
    calls = []
    def loader(deadline):
        calls.append(deadline)
        return lambda p: (False, None)
    service = wr.WorkdirRetention(root, lambda p: (False, None), snapshot_loader=loader)
    assert len(service.run(30)['candidates']) == 2
    assert len(calls) == 1
    calls.clear()
    assert service.run(30, ['orphan'], delete=True)['deleted'] == 1
    assert len(calls) == 2


def test_workspace_dirs_cannot_be_changed_in_place(tmp_path):
    workspace = srv.workspaces.Workspace('test', 'test', dirs=[str(tmp_path)])
    exposed = workspace.dirs
    exposed.append('not-a-directory')
    exposed[0] = 'changed'
    workspace.dirs.clear()
    assert workspace.dirs == [str(tmp_path)]


def test_failed_claim_is_released_and_completed_records_are_bounded(tmp_path, monkeypatch):
    root, path = old_tree(tmp_path)
    service = collector(root)
    def fail(*args):
        raise PermissionError('blocked')
    monkeypatch.setattr(service, '_remove_tree', fail)
    assert service.run(30, ['orphan'], delete=True)['deleted'] == 0
    assert not wr._claims and not wr._completed and path.exists()
    # A failed attempt does not poison a valid surviving reference.
    with wr.reference_transaction([str(path)]):
        pass
    for i in range(300):
        with wr.directory_claim(root/str(i), wr._epoch):
            pass
    assert not wr._claims and len(wr._completed) == 256


def test_reference_loader_deadline_fails_closed(tmp_path, monkeypatch):
    root, path = old_tree(tmp_path)
    monkeypatch.setattr(wr, 'MAX_SECONDS', .01)
    def slow(deadline):
        time.sleep(.03)
        return lambda p: (False, None)
    result = wr.WorkdirRetention(root, lambda p: (False, None), snapshot_loader=slow).run(30, ['orphan'], delete=True)
    assert result['error'] == 'reference_scan_limit' and path.exists()


def test_overlapping_claim_and_writer_admission_have_no_wait_path(tmp_path):
    root, path = old_tree(tmp_path)
    with wr.directory_claim(path, wr._epoch):
        started = time.monotonic()
        with pytest.raises(ValueError, match='being cleaned'):
            with wr.reference_transaction([str(path/'nested')]):
                pytest.fail('overlapping reference was admitted')
        with wr.reference_transaction([str(tmp_path/'unrelated')]):
            pass
        assert time.monotonic()-started < .5
        with pytest.raises(ValueError, match='claim_busy'):
            with wr.directory_claim(path, wr._epoch):
                pytest.fail('overlapping collector was admitted')
    assert not wr._claims


def test_live_reference_read_occurs_after_writer_registration(tmp_path):
    root, path = old_tree(tmp_path)
    with wr.directory_claim(path, wr._epoch):
        def paths():
            assert wr._writers == 1
            return [str(path)]
        with pytest.raises(ValueError, match='being cleaned'):
            with wr.reference_transaction(paths):
                pytest.fail('claimed reference was admitted')
    assert wr._writers == 0


def test_completion_eviction_cannot_admit_reference_to_missing_managed_path(tmp_path):
    root, path = old_tree(tmp_path)
    assert collector(root).run(30, ['orphan'], delete=True)['deleted'] == 1
    for i in range(300):
        with wr.directory_claim(root/str(i), wr._epoch):
            pass
    assert path not in wr._completed and len(wr._completed) == 256
    with pytest.raises(ValueError, match='missing'):
        with wr.reference_transaction([str(path)]):
            pytest.fail('missing path admitted after completion eviction')
    path.mkdir()
    with wr.reference_transaction([str(path)]):
        pass  # explicit recreation has a valid directory again


def test_cleanup_root_registry_has_a_fail_closed_bound(tmp_path, monkeypatch):
    root, path = old_tree(tmp_path)
    monkeypatch.setattr(wr, 'MAX_ROOTS', 1)
    assert collector(root).run(30)['candidates']
    other = tmp_path/'other-root'
    other.mkdir()
    assert collector(other).run(30)['error'] == 'workdir_root_limit'
    assert wr._roots == {root} and path.exists()


def test_final_reference_recheck_preserves_directory_and_releases_claim(tmp_path):
    root, path = old_tree(tmp_path)
    calls = []
    def loader(deadline):
        calls.append(deadline)
        referenced = len(calls) > 1
        return lambda p: (referenced, 'new_reference')
    result = wr.WorkdirRetention(root, lambda p: (False, None), snapshot_loader=loader).run(30, ['orphan'], delete=True)
    assert result['deleted'] == 0 and result['skipReasons'] == {'new_reference': 1}
    assert path.exists() and not wr._claims and not wr._completed


def test_symlink_reference_alias_cannot_bypass_active_claim(tmp_path):
    root, path = old_tree(tmp_path)
    alias = tmp_path/'alias'
    try:
        alias.symlink_to(path, target_is_directory=True)
    except OSError:
        import subprocess
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(path)], check=True, capture_output=True)
    with wr.directory_claim(path, wr._epoch):
        with pytest.raises(ValueError, match='being cleaned'):
            with wr.reference_transaction([str(alias)]):
                pytest.fail('alias reference was admitted')


@pytest.mark.parametrize('days', [0, -1, True, 1.2, None, 36501])
def test_api_validates_days_and_rejects_paths(tmp_path, monkeypatch, days):
    root, path = old_tree(tmp_path)
    monkeypatch.setattr(srv, '_workdir_collector', lambda: collector(root))
    with pytest.raises(srv.HTTPException):
        asyncio.run(srv.api_preview_workdir_retention({'days': days}))
    with pytest.raises(srv.HTTPException):
        asyncio.run(srv.api_delete_workdir_retention({'days': 30, 'path': str(path)}))
    assert path.exists()
