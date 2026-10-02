"""PR-inspired read-only search: exact totals without building an index first.

Sources stream canonical rows and support rereading only the selected page.
No persistent content copies, SQLite writes, background worker or warm-up.
Per-Session lightweight match references preserve last-ID-wins semantics.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import hmac
import secrets
from threading import Lock
from collections import OrderedDict

from .session import is_pan_message_id
from .history_search_index import (SEARCH_ROLES, HistorySearchCursorError,
                                   normalize_roles, bounded_limit, session_version, _snippet)

_key = None
_key_lock = Lock()
_matches_cache = OrderedDict()
_cache_lock = Lock()
_cache_references = 0
MAX_CACHE_ENTRIES = 256
MAX_CACHE_REFERENCES = 100_000


def _remember(key, value):
    global _cache_references
    if len(value[1]) > MAX_CACHE_REFERENCES:
        return
    with _cache_lock:
        old = _matches_cache.pop(key, None)
        if old is not None:
            _cache_references -= len(old[1])
        _matches_cache[key] = value
        _cache_references += len(value[1])
        while len(_matches_cache) > MAX_CACHE_ENTRIES or _cache_references > MAX_CACHE_REFERENCES:
            _, removed = _matches_cache.popitem(last=False)
            _cache_references -= len(removed[1])


def cursor_key():
    global _key
    with _key_lock:
        if _key is None:
            _key = secrets.token_bytes(32)
        return _key


@contextmanager
def memory_source(session):
    lock = getattr(session, '_summary_lock', None)
    with lock if lock is not None else nullcontext():
        history = getattr(session, 'history', []) or []
        # Loaded histories need no disk parsing and are not retained by search.
        # Avoid using recyclable Python object IDs as cache identity.
        yield ((index, row, index) for index, row in enumerate(history)), history.__getitem__


def scan_history(sessions, query, *, roles=None, limit=50, after=None,
                 match_index=None, message_id=None, source=memory_source,
                 cursor_auth=None):
    selected = normalize_roles(roles)
    needle = query.strip().casefold()
    limit = bounded_limit(limit)
    if not needle or not selected:
        return {'hits': [], 'versions': [], 'limit': limit, 'hasMore': False,
                'totalMatches': 0, 'totalMessages': 0, 'roles': list(selected), 'matchingSessionIds': []}
    key = cursor_key()
    if cursor_auth is not None:
        body, signature = cursor_auth
        if not hmac.compare_digest(hmac.new(key, body.encode('ascii'), hashlib.sha256).digest(), signature):
            raise HistorySearchCursorError('search cursor signature is invalid')
    versions, hits, matching_session_ids = [], [], []
    total_matches, total_messages = 0, 0
    for scope_position, session in enumerate(sessions):
        epoch, revision, known_total, _ = session_version(session)
        with source(session) as opened:
            rows, read_row = opened[:2]
            stamp = opened[2] if len(opened) > 2 else None
            cache_key = (session.id, epoch, revision, known_total, stamp, selected, needle) if stamp is not None and known_total is not None else None
            with _cache_lock:
                cached = _matches_cache.get(cache_key) if cache_key is not None else None
                if cached is not None:
                    _matches_cache.move_to_end(cache_key)
            matches = {}
            total = 0
            for index, row, locator in (() if cached is not None else rows):
                total = index + 1
                if not isinstance(row, dict):
                    continue
                identity = row.get('messageId')
                role = row.get('role')
                if role not in SEARCH_ROLES or not isinstance(identity, str):
                    continue
                # A later logical block with this ID supersedes earlier text,
                # including when it no longer matches the selected roles/query.
                matches.pop(identity, None)
                if role not in selected or row.get('source') == 'system_prompt':
                    continue
                text = row.get('content')
                if not isinstance(text, str) or not text.strip():
                    continue
                folded = text.casefold()
                count = folded.count(needle)
                if count and is_pan_message_id(identity):
                    matches[identity] = (index, locator, role, count)
            if cached is not None:
                total, ordered_matches = cached
            else:
                ordered_matches = sorted(matches.items(), key=lambda item: item[1][0])
                if cache_key is not None:
                    _remember(cache_key, (total, ordered_matches))
            version = {'sessionId': session.id, 'historyEpoch': epoch,
                       'historyRevision': revision, 'historyTotal': total}
            versions.append(version)
            if ordered_matches:
                matching_session_ids.append(session.id)
            for identity, (index, locator, role, count) in ordered_matches:
                start = total_matches
                total_matches += count
                total_messages += 1
                if len(hits) >= limit+1:
                    continue
                if after is not None and (scope_position, index) <= after:
                    continue
                if match_index is not None and start+count <= match_index:
                    continue
                if message_id is not None and identity != message_id:
                    continue
                row = read_row(locator)
                text = row['content']
                offset = text.casefold().find(needle)
                if len(text.casefold()) != len(text):
                    consumed = 0
                    for position, character in enumerate(text):
                        consumed += len(character.casefold())
                        if consumed > offset:
                            offset = position
                            break
                hits.append({**version, 'messageId': identity, 'messageIndex': index,
                             'role': role, 'snippet': _snippet(text, needle, offset),
                             'matchCount': count, 'matchStart': start, 'firstMatch': offset,
                             '_scopePosition': scope_position})
    next_after = None
    if len(hits) > limit:
        last = hits[limit-1]
        next_after = (last['_scopePosition'], last['messageIndex'])
    for hit in hits:
        hit.pop('_scopePosition')
    result = {'hits': hits[:limit], 'versions': versions, 'limit': limit,
              'hasMore': len(hits) > limit, 'totalMatches': total_matches,
              'totalMessages': total_messages, 'roles': list(selected), '_cursorKey': key,
              'matchingSessionIds': matching_session_ids}
    if next_after is not None:
        result['nextAfter'] = next_after
    return result
