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


def install_query_schema(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE history_search_queries (
        query_key TEXT PRIMARY KEY, total_matches INTEGER NOT NULL,
        total_messages INTEGER NOT NULL, created_at REAL NOT NULL)''')
    connection.execute('''CREATE TABLE history_search_query_hits (
        query_key TEXT NOT NULL, position INTEGER NOT NULL,
        message_rowid INTEGER NOT NULL, scope_position INTEGER NOT NULL,
        message_index INTEGER NOT NULL, match_count INTEGER NOT NULL,
        match_start INTEGER NOT NULL, first_match INTEGER NOT NULL,
        PRIMARY KEY(query_key, position))''')
    connection.execute('CREATE INDEX history_search_query_seek ON history_search_query_hits '
                       '(query_key, scope_position, message_index)')
    connection.execute('CREATE INDEX history_search_query_match ON history_search_query_hits '
                       '(query_key, match_start)')


def clear_query_cache(connection: sqlite3.Connection) -> None:
    connection.execute('DELETE FROM history_search_query_hits')
    connection.execute('DELETE FROM history_search_queries')


def _candidate_sql(needle: str, roles: tuple[str, ...], tables: dict[str, str]):
    role_params = ','.join('?' for _ in roles)
    columns = ('message.rowid AS message_rowid, message.message_index, '
               'scope.position AS scope_position, message.folded_content')
    joins = ('JOIN history_search_messages AS message ON message.rowid=fts.rowid '
             'JOIN temp.history_search_scope AS scope ON scope.session_id=message.session_id ')
    # Trigram has no useful candidates for 1/2 characters. Keep full literal
    # coverage without widening the scan to unselected roles.
    # MATCH's parser treats embedded NUL as a string terminator; instr accepts
    # it literally. Do not turn an otherwise valid content query into a 503.
    if len(needle) < 3 or '\x00' in needle:
        return (f'SELECT {columns} FROM history_search_messages AS message '
                'JOIN temp.history_search_scope AS scope ON scope.session_id=message.session_id '
                f'WHERE message.role IN ({role_params}) AND instr(message.folded_content, ?) > 0',
                (*roles, needle))
    phrase = '"' + needle.replace('"', '""') + '"'
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
        connection.execute('DELETE FROM history_search_query_hits WHERE query_key=?', (row['query_key'],))
        connection.execute('DELETE FROM history_search_queries WHERE query_key=?', (row['query_key'],))


def cached_query_page(connection: sqlite3.Connection, needle: str, roles: tuple[str, ...],
                      tables: dict[str, str], versions: list[dict], limit: int,
                      after: tuple[int, int] | None = None,
                      match_index: int | None = None) -> tuple[list[sqlite3.Row], dict]:
    key = hashlib.sha256(json.dumps([needle, roles, versions], sort_keys=True,
                                   separators=(',', ':')).encode()).hexdigest()
    sql, params = _candidate_sql(needle, roles, tables)
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
                _evict(connection, 0)
                total, count = 0, 0
                cursor = connection.execute(sql + ' ORDER BY scope_position, message_index', params)
                oversized = False
                references = []
                for row in cursor:
                    matches = row['folded_content'].count(needle)
                    if count < MAX_CACHED_REFERENCES:
                        references.append((key, count, row['message_rowid'], row['scope_position'],
                                           row['message_index'], matches, total,
                                           row['folded_content'].find(needle)))
                    else:
                        oversized = True
                    total += matches
                    count += 1
                if oversized:
                    connection.execute('DELETE FROM history_search_query_hits WHERE query_key=?', (key,))
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
                connection.executemany('INSERT INTO history_search_query_hits VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                                       references)
                connection.execute('INSERT INTO history_search_queries VALUES (?, ?, ?, ?)',
                                   (key, total, count, time.time()))
                meta = {'total_matches': total, 'total_messages': count}
        except BaseException:
            connection.rollback()
            raise
    clause, seek = _seek(after, match_index, 'hit.')
    rows = connection.execute(
        'SELECT hit.*, message.* FROM history_search_query_hits hit '
        'JOIN history_search_messages message ON message.rowid=hit.message_rowid '
        'WHERE hit.query_key=? ' + clause.replace('WHERE ', 'AND ', 1) +
        ' ORDER BY hit.position LIMIT ?', (key, *seek, limit+1)).fetchall()
    connection.commit()
    return rows, {'totalMatches': meta['total_matches'], 'totalMessages': meta['total_messages'], 'queryCached': True}


def _seek(after, match_index, prefix=''):
    if match_index is not None:
        return f'WHERE {prefix}match_start+{prefix}match_count > ?', (match_index,)
    if after is not None:
        return (f'WHERE ({prefix}scope_position > ? OR '
                f'({prefix}scope_position = ? AND {prefix}message_index > ?))',
                (after[0], after[0], after[1]))
    return '', ()
