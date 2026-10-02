"""Reproducible synthetic core benchmark, optionally against PR #3's helper.

Run from the repository root: py -3.14 scripts/benchmark_history_search_content.py
--compare-pr downloads only a pinned source function through authenticated gh.
No Pan configuration, registry, service, or production history is accessed.
Temporary JSONL/SQLite files are automatically removed. Output is JSON stdout.
This is not HTTP/E2E evidence or a claim about production latency.
"""
import argparse
import ast
import hashlib
import functools
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.core.history_search_index import search_history
from packages.core import history_search_index

PR_HEAD = 'bf811a7cc2f0271023b459729b7fb0079591ebaf'
_stage_samples = {}


def record_stage(function, label):
    @functools.wraps(function)
    def timed(*args, **kwargs):
        start = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            _stage_samples.setdefault(label, []).append((time.perf_counter()-start)*1000)
    return timed


def measure(function, rounds=7):
    samples = []
    stage_rounds = []
    for _ in range(rounds):
        _stage_samples.clear()
        start = time.perf_counter()
        result = function()
        samples.append((time.perf_counter() - start) * 1000)
        stage_rounds.append({name: sum(values) for name, values in _stage_samples.items()})
    stages = {name: statistics.median(round.get(name, 0) for round in stage_rounds)
              for name in set().union(*(round.keys() for round in stage_rounds))}
    return {'medianMs': statistics.median(samples), 'samplesMs': samples,
            **({'inclusiveStageMedianMs': stages} if stages else {})}, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare-pr', action='store_true')
    parser.add_argument('--rows', type=int, default=10_000)
    parser.add_argument('--profile-stages', action='store_true',
                        help='Report inclusive loader/snapshot/index/cache stage timings, not additive costs')
    args = parser.parse_args()
    if args.profile_stages:
        for name in ('_snapshot', '_replace_session', '_append_session',
                     '_insert_messages', '_index_inserted_rows',
                     '_ensure_schema', 'cached_query_page'):
            setattr(history_search_index, name, record_stage(getattr(history_search_index, name), name))
    if not 1 <= args.rows <= 100_000:
        parser.error('--rows must be between 1 and 100000')
    helper = None
    if args.compare_pr:
        source = subprocess.run(['gh', 'api',
            'repos/Thelittlewinter233/pan/contents/packages/web/server.py?ref=' + PR_HEAD,
            '-H', 'Accept: application/vnd.github.raw+json'], check=True,
            capture_output=True).stdout.decode('utf-8')
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == '_search_session_history')
        helper = ast.get_source_segment(source, function)
    report = {'python': sys.version, 'sqlite': sqlite3.sqlite_version,
              'platform': platform.platform(), 'rows': args.rows,
              'method': 'File-backed cold Session loader; cached OS files; core functions only; '
                        '100 message references/page, exact full occurrence totals; seven warm rounds.',
              'prHead': PR_HEAD if helper else None,
              'prHelperSha256': hashlib.sha256(helper.encode()).hexdigest() if helper else None}
    with tempfile.TemporaryDirectory(prefix='pan-search-content-bench-') as temporary:
        directory = Path(temporary)
        history_path = directory / 'bench.history.jsonl'
        rows = []
        for i in range(args.rows):
            text = f'commonterm commonterm message {i} ' + 'ordinary evidence ' * 10
            if i % 500 == 0:
                text += ' selectiveterm'
            if i % 11 == 0:
                text += ' \u4e2d'
            if i % 29 == 0:
                text += ' !?+@'
            rows.append({'role': ('user', 'assistant', 'tool', 'thinking')[i % 4],
                         'content': text, 'messageId': f'pan:{i+1:032x}'})
        history_path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
        original = hashlib.sha256(history_path.read_bytes()).hexdigest()
        meta = SimpleNamespace(id='bench', history_epoch='bench', history_revision=1,
                               _history_loaded=False, summary_projection={'history_total': args.rows})
        def loader(_):
            return SimpleNamespace(id='bench', history_epoch=meta.history_epoch,
                                   history_revision=meta.history_revision, _history_loaded=True,
                                   history=[json.loads(line) for line in history_path.read_text(encoding='utf-8').splitlines()])
        if args.profile_stages:
            loader = record_stage(loader, 'canonicalLoader')
        path = directory / 'index.sqlite3'
        def ours(query, roles=('user', 'assistant', 'tool', 'thinking'), after=None, target=path):
            return search_history(target, [meta], ['bench'], query, limit=100,
                                  load_session=loader, roles=roles, content_counts=True, after=after)
        pr = None
        if helper:
            namespace = {'json': json, '_NOT_FOUND': object(), '_summary_session_get': lambda _: True,
                         'sess': SimpleNamespace(_history_path=lambda _: history_path,
                                                 _path=lambda _: directory / 'missing.json'),
                         '_api_history': lambda _, entries, **kwargs: [dict(row) for row in entries]}
            exec(compile(helper, 'PR3-pinned-search-function', 'exec'), namespace)
            pr = lambda query: namespace['_search_session_history']('bench', query, 100)
        counter = 0
        def cold():
            nonlocal counter
            counter += 1
            return ours('selectiveterm', target=directory / f'cold-{counter}.sqlite3')
        report['coldIndexAndFirstQuery'], _ = measure(cold, 3)
        report['queries'] = {}
        for query in ('selectiveterm', 'commonterm', '\u4e2d', '!?+@'):
            initial, result = measure(lambda: ours(query), 1)
            warm, result = measure(lambda: ours(query))
            expected = sum(row['content'].casefold().count(query.casefold()) for row in rows)
            assert result['totalMatches'] == expected
            data = {'firstQuery': initial, 'warm': warm, 'totalMatches': expected}
            if pr:
                data['pr'], comparison = measure(lambda: pr(query))
                assert comparison['totalMatches'] == expected
                assert [hit['messageIndex'] for hit in result['hits']] == [hit['index'] for hit in comparison['matches']]
            # Different role selection is a separate cache; count and compare
            # its first matching pass and subsequent cached reads independently.
            data['bodyFirstQuery'], filtered = measure(lambda: ours(query, ('user', 'assistant')), 1)
            data['bodyWarm'], filtered = measure(lambda: ours(query, ('user', 'assistant')))
            assert filtered['totalMatches'] == sum(r['content'].casefold().count(query.casefold())
                                                 for r in rows if r['role'] in ('user', 'assistant'))
            report['queries'][query] = data
        def paginate():
            after, messages, pages = None, 0, 0
            while True:
                page = ours('commonterm', after=after)
                messages += len(page['hits'])
                pages += 1
                if not page['hasMore']:
                    break
                after = page['nextAfter']
            assert messages == args.rows
            return pages
        report['allDensePages'], report['pageCount'] = measure(paginate, 3)
        report['sqliteBytes'] = path.stat().st_size
        report['jsonlBytes'] = history_path.stat().st_size
        assert hashlib.sha256(history_path.read_bytes()).hexdigest() == original
        append_ours, append_pr, append_stages = [], [], []
        for i in range(3):
            with history_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'role': 'tool', 'content': 'selectiveterm appended',
                                         'messageId': f'pan:{args.rows+i+1:032x}'})+'\n')
            meta.history_revision += 1
            meta.summary_projection['history_total'] += 1
            sample, _ = measure(lambda: ours('selectiveterm'), 1)
            append_ours.extend(sample['samplesMs'])
            append_stages.append(sample.get('inclusiveStageMedianMs', {}))
            if pr:
                sample, _ = measure(lambda: pr('selectiveterm'), 1)
                append_pr.extend(sample['samplesMs'])
        report['appendFirstQuery'] = {'oursMedianMs': statistics.median(append_ours),
                                     'oursSamplesMs': append_ours,
                                     'prMedianMs': statistics.median(append_pr) if append_pr else None}
        if args.profile_stages:
            report['appendFirstQuery']['inclusiveStageMedianMs'] = {
                name: statistics.median(stage.get(name, 0) for stage in append_stages)
                for name in set().union(*(stage.keys() for stage in append_stages))}
    report['temporaryDataRemoved'] = not directory.exists()
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
