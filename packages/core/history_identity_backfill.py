"""On-demand, ticket-ordered identity repair of canonical Pan history.

No adapter import, resident worker or full-history cache hydration. Invalid
JSONL lines are retained byte-for-byte. Only missing searchable identities
are added. An interrupted temporary write cannot truncate the source.
"""
import copy
import json
import os
import tempfile
from collections import deque

from . import session as store


def prepare_history_identities(session):
    state, ticket, queued = store._reserve_save_ticket(session.id)
    return store._run_persistence_ticket(
        state, ticket, queued, lambda: _prepare(session))


def _stamp(session):
    path = store._history_path(session.id)
    signature = store._history_file_signature(path)
    return (str(path.resolve()), signature, session.history_epoch,
            session.history_revision, len(session.history) if session._history_loaded else None)


def _add_identity(row):
    if (isinstance(row, dict) and store._is_searchable_history_body(row)
            and not store.is_pan_message_id(row.get('messageId'))):
        store._remember_steer_client_identity(row)
        row['messageId'] = store._new_pan_message_id()
        return True
    return False


def _prepare(session):
    if not store._path(session.id).is_file():
        raise FileNotFoundError('Session no longer exists')
    # Cold hydration also uses this lock; it must not replace a repaired view.
    with session._summary_lock:
        if getattr(session, '_history_ids_ready', None) == _stamp(session):
            return 0
        if not session._history_loaded and session.history:
            store._prepare_history_for_save(session, store._history_path(session.id), force_full=True)
        loaded = session._history_loaded
        if loaded:
            missing = any(isinstance(row, dict) and store._is_searchable_history_body(row)
                          and not store.is_pan_message_id(row.get('messageId')) for row in session.history)
            if not missing:
                if getattr(session, '_history_ids_metadata_pending', False):
                    data = session.to_dict()
                    data.pop('system_prompt', None)
                    data['history'] = session.history[-store._MAIN_HISTORY_TAIL:]
                    _write_metadata(session, data)
                session._history_ids_ready = _stamp(session)
                return 0
            baseline = copy.deepcopy(session.history)
            epoch = session.history_epoch
            file_signature = store._history_file_signature(store._history_path(session.id))
        else:
            return _prepare_cold(session)
    rows = copy.deepcopy(baseline)
    count = sum(_add_identity(row) for row in rows)
    if not count:
        with session._summary_lock:
            if session.history == baseline:
                if getattr(session, '_history_ids_metadata_pending', False):
                    data = session.to_dict()
                    data.pop('system_prompt', None)
                    data['history'] = session.history[-store._MAIN_HISTORY_TAIL:]
                    _write_metadata(session, data)
                session._history_ids_ready = _stamp(session)
        return 0
    path = store._history_path(session.id)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=store.SESSION_DIR, suffix='.ids.tmp', delete=False) as output:
            temporary = output.name
            persisted = 0
            if path.exists():
                with path.open('rb') as source:
                    for line in source:
                        try:
                            row = json.loads(line.decode('utf-8'))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            output.write(line)
                            continue
                        if persisted >= len(baseline):
                            raise RuntimeError('Canonical history advanced; retry search')
                        repaired = rows[persisted]
                        if isinstance(row, dict) and store._strip_delivery_marks([dict(row)])[0] == baseline[persisted]:
                            changed = repaired != baseline[persisted]
                            if changed:
                                row.update({key: repaired[key] for key in ('messageId', 'clientMessageId') if key in repaired})
                            output.write(store._encode_line(row) if changed else line)
                        else:
                            output.write(store._encode_line(repaired))
                        persisted += 1
            for row in rows[persisted:]:
                output.write(store._encode_line(row))
            # Serialize only the short commit, not the bulk write. Appends made
            # while writing remain in the same list and are included here.
            with session._summary_lock:
                if (session.history_epoch != epoch
                        or session.history[:len(baseline)] != baseline
                        or store._history_file_signature(path) != file_signature):
                    raise RuntimeError('History changed while preparing identities; retry search')
                suffix = session.history[len(baseline):]
                for row in suffix:
                    output.write(store._encode_line(row))
                output.flush()
                os.fsync(output.fileno())
                output.close()  # Windows cannot replace an open temporary file.
                os.replace(temporary, path)
                store._newline_terminated_jsonl.discard(str(path))
                for original, repaired in zip(session.history, rows):
                    if isinstance(repaired, dict) and repaired != original:
                        original.update({key: repaired[key] for key in ('messageId', 'clientMessageId') if key in repaired})
                session._hist_persisted = len(session.history)
                _advance_version(session)
                data = session.to_dict()
                data.pop('system_prompt', None)
                data['history'] = session.history[-store._MAIN_HISTORY_TAIL:]
                _write_metadata(session, data)
                session._history_ids_ready = _stamp(session)
        return count
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _advance_version(session):
    session.history_revision += 1
    session.summary_projection['revision'] = int(session.summary_projection.get('revision') or 0) + 1
    session._history_ids_metadata_pending = True
    session._last_meta_sig = None


def _write_metadata(session, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=store.SESSION_DIR,
                                         suffix='.ids-meta.tmp', delete=False) as output:
            temporary = output.name
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, store._path(session.id))
        session._last_meta_sig = store._meta_signature(session)
        session._history_ids_metadata_pending = False
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _prepare_cold(session):
    path = store._history_path(session.id)
    data = json.loads(store._path(session.id).read_text(encoding='utf-8'))
    # A modern Session needs only a read-only check once per process/stamp;
    # never write a full throwaway file when every eligible block has an ID.
    if path.exists() and not getattr(session, '_history_ids_metadata_pending', False):
        signature = store._history_file_signature(path)
        missing = False
        with path.open('rb') as source:
            for line in source:
                try:
                    row = json.loads(line.decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if (isinstance(row, dict) and store._is_searchable_history_body(row)
                        and not store.is_pan_message_id(row.get('messageId'))):
                    missing = True
                    break
        if store._history_file_signature(path) != signature:
            raise RuntimeError('Canonical history changed; retry search')
        if not missing:
            session._history_ids_ready = _stamp(session)
            return 0
    count = 0
    file_signature = store._history_file_signature(path)
    tail = deque(maxlen=store._MAIN_HISTORY_TAIL)
    projection = store._summary_projection_from_history([])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=store.SESSION_DIR, suffix='.ids.tmp', delete=False) as output:
            temporary = output.name
            if path.exists():
                with path.open('rb') as source:
                    for line in source:
                        try:
                            row = json.loads(line.decode('utf-8'))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            output.write(line)
                            continue
                        changed = _add_identity(row)
                        count += changed
                        output.write(store._encode_line(row) if changed else line)
                        if isinstance(row, dict):
                            tail.append(row)
                            store._apply_summary_message(projection, row)
            else:
                for original in data.get('history', []):
                    row = copy.deepcopy(original)
                    count += _add_identity(row)
                    output.write(store._encode_line(row))
                    if isinstance(row, dict):
                        tail.append(row)
                        store._apply_summary_message(projection, row)
            if count:
                output.flush()
                os.fsync(output.fileno())
                output.close()
                if store._history_file_signature(path) != file_signature:
                    raise RuntimeError('Canonical history changed; retry search')
                os.replace(temporary, path)
                store._newline_terminated_jsonl.discard(str(path))
                _advance_version(session)
                projection['revision'] = session.summary_projection['revision']
                projection['updated_at'] = session.updated_at
                session.summary_projection = projection
                session._summary_projection_complete = True
                session._history_tail = list(tail)
                data.update(history=list(tail), history_revision=session.history_revision,
                            summary_projection=projection)
                _write_metadata(session, data)
            elif getattr(session, '_history_ids_metadata_pending', False):
                data.update(history=session._history_tail,
                            history_revision=session.history_revision,
                            summary_projection=session.summary_projection)
                _write_metadata(session, data)
            session._history_ids_ready = _stamp(session)
        return count
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
