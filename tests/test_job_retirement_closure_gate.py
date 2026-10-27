"""Non-vacuous inventory checks and conservative retirement decision contract."""
import json
from pathlib import Path

from scripts.job_retirement_closure import decision, scan

ROOT = Path(__file__).resolve().parents[1]


def test_relative_and_aliased_dynamic_imports_are_visible(tmp_path):
    path = tmp_path / 'packages/scheduler/consumer.py'
    path.parent.mkdir(parents=True)
    path.write_text('from . import store\nfrom packages.core import background_jobs\n'
                    'from importlib import import_module as load\n'
                    'load("packages.scheduler.time_events")\n', encoding='utf-8')
    result = scan(tmp_path, ['packages/scheduler/consumer.py'])
    assert {r['module'] for r in result['dependencyEdges']} == {
        'packages.scheduler.store', 'packages.core.background_jobs'}
    # Aliased calls cannot be treated as absent merely because their name changed.
    # All dynamic aliases must be resolved by the inventory, not a zero scan.
    assert any('time_events' in r['code'] for r in result['dynamicCandidates'])


def test_empty_inventory_cannot_authorize_deletion(tmp_path):
    result = scan(tmp_path, [])
    verdict = decision(result, {'originalNodes': []})
    assert verdict['exitCode'] == 2 and verdict['removalAllowed'] is False
    assert verdict['migrationComplete'] is False
    assert 'Incomplete repository inventory' in verdict['blockers']
    assert any('identity/owner' in text for text in verdict['blockers'])


def test_parse_failure_and_missing_entrypoints_are_not_zero_dependencies(tmp_path):
    path = tmp_path / 'packages/broken.py'
    path.parent.mkdir()
    path.write_text('def broken(', encoding='utf-8')
    result = scan(tmp_path, ['packages/broken.py'])
    assert len(result['errors']) == 1 and result['missingRequired']
    assert decision(result, {'originalNodes': []})['removalAllowed'] is False


def test_mapping_covers_every_original_node_and_inventory_identity():
    mapping = json.loads((ROOT / 'evidence/job-retirement-closure/successors.json').read_text(encoding='utf-8'))
    original = json.loads((ROOT / 'evidence/r6i-repair/original-red-assessment.json').read_text(encoding='utf-8'))
    nodes = {r['node']: r for r in mapping['originalNodes']}
    assert len(nodes) == len(mapping['originalNodes']) == 43
    assert {r['node'] for r in original['nodes']} <= nodes.keys()
    assert {r['id'] for r in mapping['retirementItems']} == {
        f'{prefix}{number:02d}' for prefix, maximum in [('E', 15), ('S', 14), ('U', 16)]
        for number in range(1, maximum + 1)}
    for row in nodes.values():
        assert row['boundary']
        if row['status'] == 'strict-successor':
            assert row['strictSuccessors']
        for node in row['strictSuccessors']:
            path, name = node.split('::')
            assert ('def ' + name.split('[')[0] + '(') in (ROOT / path).read_text(encoding='utf-8')


def test_data_directories_are_never_inventory_inputs(tmp_path):
    for relative in ('data/private.py', 'profiles/private.py', 'evidence/private.py'):
        path = tmp_path / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'\xff')
    result = scan(tmp_path, ['data/private.py', 'profiles/private.py', 'evidence/private.py'])
    assert result['files'] == [] and result['errors'] == []
