"""REST and MCP share a public, typed natural-exit read projection."""
import pytest

from packages.web.terminal_api import project_read


@pytest.mark.parametrize('code,complete,seen,expected', [
    (7, False, True, {'exit_code': 7, 'output_complete': False, 'process_exit_seen': True}),
    (4294967295, True, True, {'exit_code': 4294967295, 'output_complete': True, 'process_exit_seen': True}),
    (True, 'true', 'true', {'exit_code': None, 'output_complete': None, 'process_exit_seen': None}),
    (-1, None, None, {'exit_code': None, 'output_complete': None, 'process_exit_seen': None}),
    (4294967296, 1, 1, {'exit_code': None, 'output_complete': None, 'process_exit_seen': None}),
])
def test_read_exit_facts_are_public_typed_and_do_not_upgrade_unknown(code, complete, seen, expected):
    result = project_read({'terminal_id': 'term_exit', 'status': 'exited', 'data': b'',
                           'exit_code': code, 'output_complete': complete,
                           'process_exit_seen': seen, 'reader_done': True,
                           'token': 'MUST_NOT_BE_EXPORTED', 'pipe': 'private'})
    for key, value in expected.items():
        assert result[key] == value and type(result[key]) is type(value)
    assert result['reader_done'] is True
    assert 'MUST_NOT_BE_EXPORTED' not in str(result) and 'pipe' not in result


def test_read_without_exit_evidence_does_not_invent_metadata():
    result = project_read({'status': 'running', 'data': b'TAIL'})
    assert result['data_b64'] == 'VEFJTA=='
    assert not {'exit_code', 'output_complete', 'process_exit_seen', 'reader_done'} & result.keys()
