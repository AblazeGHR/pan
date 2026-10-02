"""Bounded, disposable query references; no content copies or resident workers.

The index owner invalidates these references before replacing/appending rows.
Keys bind literal query, selected roles, ordered scope and canonical versions.
Only selected FTS partitions are read. A page never repeats content matching
when its query references remain valid.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

MAX_CACHED_QUERIES = 16
MAX_CACHED_REFERENCES = 100_000
REFERENCE_CHUNK_SIZE = 128


def install_query_schema(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE history_search_queries (
        query_key TEXT PRIMARY KEY, total_matches INTEGER NOT NULL,
        total_messages INTEGER NOT NULL, created_at REAL NOT NULL)''')
    connection.execute('''CREATE TABLE history_search_query_chunks (
        query_key TEXT NOT NULL, position INTEGER NOT NULL,
        scope_end INTEGER NOT NULL, index_end INTEGER NOT NULL,
        match_end INTEGER NOT NULL, references_json TEXT NOT NULL,
        PRIMARY KEY(query_key, position))''')


def clear_query_cache(connection: sqlite3.Connection) -> None:
    connection.execute('DELETE FROM history_search_query_chunks')
    connection.execute('DELETE FROM history_search_queries')


def _candidate_sql(needle: str, roles: tuple[str, ...], tables: dict[str, str], connection=None):
    role_params = ','.join('?' for _ in roles)
    columns = ('message.rowid AS message_rowid, message.message_index, '
               'scope.position AS scope_position, message.folded_content')
    joins = ('JOIN history_search_messages AS message ON message.rowid=fts.rowid '
             'JOIN temp.history_search_scope AS scope ON scope.session_id=message.session_id ')
    # Trigram has no useful candidates for 1/2 characters. Keep full literal
    # coverage without widening the scan to unselected roles.
    # MATCH's parser treats embedded NUL as a string terminator; instr accepts
    # it literally. Do not turn an otherwise valid content query into a 503.
    def literal_scan():
        return (f'SELECT {columns} FROM history_search_messages AS message '
                'JOIN temp.history_search_scope AS scope ON scope.session_id=message.session_id '
                f'WHERE message.role IN ({role_params}) AND instr(message.folded_content, ?) > 0',
                (*roles, needle))
    if len(needle) < 3 or '\x00' in needle:
        return literal_scan()
    # Positionless trigram indexes need only document membership. Intersect
    # bounded 3-character anchors, then verify the complete literal below.
    # This avoids storing every occurrence's FTS token positions. The anchor
    # intersection is a superset of literal matches, never a truncated search.
    starts = (0, (len(needle) - 3) // 2, len(needle) - 3)
    anchors = dict.fromkeys(needle[start:start+3] for start in starts)
    phrase = ' AND '.join('"' + anchor.replace('"', '""') + '"' for anchor in anchors)
    if connection is not None:
        # A dense posting list adds FTS joins/sorts without narrowing the scan.
        # Vocabulary statistics are cheap and affect the plan, never coverage.
        estimated = 0
        placeholders = ','.join('?' for _ in anchors)
        for table in dict.fromkeys(tables[role] for role in roles):
            frequencies = connection.execute(f'SELECT doc FROM {table}_vocab '
                                             f'WHERE term IN ({placeholders})', tuple(anchors)).fetchall()
            if len(frequencies) == len(anchors):
                estimated += min(row['doc'] for row in frequencies)
        indexed_total = connection.execute('SELECT count(*) FROM history_search_messages').fetchone()[0]
        if estimated > max(512, indexed_total // 3):
            return literal_scan()
    branches, params = [], []
    for table in dict.fromkeys(tables[role] for role in roles):
        branches.append(f'SELECT {columns} FROM {table} AS fts {joins}'
                        f'WHERE {table} MATCH ? AND message.role IN ({role_params}) '
                        'AND instr(message.folded_content, ?) > 0')
        params.extend((phrase, *roles, needle))
    return ' UNION ALL '.join(branches), tuple(params)


def _evict(connection: sqlite3.Connection, incoming: int) -> None:
    existing = connection.execute('SELECT query_key, total_messages FROM history_search_queries '
                                  'ORDER BY created_at').fetchall()
    count = sum(row['total_messages'] for row in existing)
    while existing and (len(existing) >= MAX_CACHED_QUERIES or count + incoming > MAX_CACHED_REFERENCES):
        row = existing.pop(0)
        count -= row['total_messages']
        connection.execute('DELETE FROM history_search_query_chunks WHERE query_key=?', (row['query_key'],))
        connection.execute('DELETE FROM history_search_queries WHERE query_key=?', (row['query_key'],))


def cached_query_page(connection: sqlite3.Connection, needle: str, roles: tuple[str, ...],
                      tables: dict[str, str], versions: list[dict], limit: int,
                      after: tuple[int, int] | None = None,
                      match_index: int | None = None) -> tuple[list[sqlite3.Row | dict], dict]:
    key = hashlib.sha256(json.dumps([needle, roles, versions], sort_keys=True,
                                   separators=(',', ':')).encode()).hexdigest()
    # Keep metadata and page references in one read snapshot. Concurrent cache
    # eviction must never turn an otherwise valid page into an empty result.
    connection.execute('BEGIN')
    meta = connection.execute('SELECT * FROM history_search_queries WHERE query_key=?', (key,)).fetchone()
    # Scope contains only registry-confirmed Sessions in their ordered scope.
    # A query cache miss is serialized with indexing; no long-lived connection.
    if meta is None:
        connection.rollback()
        connection.execute('BEGIN IMMEDIATE')
        try:
            meta = connection.execute('SELECT * FROM history_search_queries WHERE query_key=?', (key,)).fetchone()
            if meta is None:
                sql, params = _candidate_sql(needle, roles, tables, connection)
                _evict(connection, 0)
                total, count = 0, 0
                cursor = connection.execute(sql + ' ORDER BY scope_position, message_index', params)
                oversized = False
                references = []
                for row in cursor:
                    matches = row['folded_content'].count(needle)
                    if count < MAX_CACHED_REFERENCES:
                        references.append((row['message_rowid'], row['scope_position'],
                                           row['message_index'], matches, total,
                                           row['folded_content'].find(needle)))
                    else:
                        oversized = True
                    total += matches
                    count += 1
                if oversized:
                    # Oversized queries stay bounded: SQLite computes a window
                    # for this request instead of retaining unlimited references.
                    connection.create_function('pan_occurrences', 1, lambda text: text.count(needle))
                    window = ('WITH candidates AS (' + sql + '), counted AS ('
                              'SELECT *, pan_occurrences(folded_content) AS match_count FROM candidates), '
                              'ordered AS (SELECT *, ROW_NUMBER() OVER (ORDER BY scope_position, message_index)-1 AS position, '
                              'COALESCE(SUM(match_count) OVER (ORDER BY scope_position, message_index '
                              'ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS match_start '
                              'FROM counted) SELECT ordered.*, instr(ordered.folded_content, ?)-1 AS first_match, message.* '
                              'FROM ordered JOIN history_search_messages message ON message.rowid=ordered.message_rowid ')
                    clause, seek = _seek(after, match_index, 'ordered.')
                    rows = connection.execute(window + clause + ' ORDER BY ordered.position LIMIT ?',
                                              (*params, needle, *seek, limit+1)).fetchall()
                    connection.commit()
                    return rows, {'totalMatches': total, 'totalMessages': count, 'queryCached': False}
                _evict(connection, count)
                def chunks():
                    for start in range(0, len(references), REFERENCE_CHUNK_SIZE):
                        chunk = references[start:start+REFERENCE_CHUNK_SIZE]
                        last = chunk[-1]
                        yield (key, start, last[1], last[2], last[4]+last[3],
                               json.dumps(chunk, separators=(',', ':')))
                connection.executemany('INSERT INTO history_search_query_chunks VALUES (?, ?, ?, ?, ?, ?)', chunks())
                connection.execute('INSERT INTO history_search_queries VALUES (?, ?, ?, ?)',
                                   (key, total, count, time.time()))
                meta = {'total_matches': total, 'total_messages': count}
        except BaseException:
            connection.rollback()
            raise
    rows = _chunk_page(connection, key, limit, after, match_index)
    connection.commit()
    return rows, {'totalMatches': meta['total_matches'], 'totalMessages': meta['total_messages'], 'queryCached': True}


def _chunk_page(connection, key, limit, after, match_index):
    """Read only enough bounded chunks for one page, within the same snapshot."""
    clause, parameters = '', ()
    if match_index is not None:
        clause, parameters = 'AND match_end>?', (match_index,)
    elif after is not None:
        clause = 'AND (scope_end>? OR (scope_end=? AND index_end>?))'
        parameters = (after[0], after[0], after[1])
    # The first chunk can contain just one remaining reference. Each following
    # chunk is full (except the final one); this bound always supplies limit+1.
    chunk_limit = (limit + REFERENCE_CHUNK_SIZE - 1) // REFERENCE_CHUNK_SIZE + 1
    chunks = connection.execute('SELECT references_json FROM history_search_query_chunks '
                                'WHERE query_key=? '+clause+' ORDER BY position LIMIT ?',
                                (key, *parameters, chunk_limit)).fetchall()
    references = []
    for chunk in chunks:
        for reference in json.loads(chunk['references_json']):
            if match_index is not None and reference[4]+reference[3] <= match_index:
                continue
            if match_index is None and after is not None and (reference[1], reference[2]) <= after:
                continue
            references.append(reference)
            if len(references) == limit+1:
                break
        if len(references) == limit+1:
            break
    if not references:
        return []
    placeholders = ','.join('?' for _ in references)
    messages = {row['rowid']: dict(row) for row in connection.execute(
        f'SELECT rowid,* FROM history_search_messages WHERE rowid IN ({placeholders})',
        tuple(reference[0] for reference in references)).fetchall()}
    return [{**messages[reference[0]], 'scope_position': reference[1],
             'match_count': reference[3], 'match_start': reference[4], 'first_match': reference[5]}
            for reference in references]


def _seek(after, match_index, prefix=''):
    if match_index is not None:
        return f'WHERE {prefix}match_start+{prefix}match_count > ?', (match_index,)
    if after is not None:
        return (f'WHERE ({prefix}scope_position > ? OR '
                f'({prefix}scope_position = ? AND {prefix}message_index > ?))',
                (after[0], after[0], after[1]))
    return '', ()
