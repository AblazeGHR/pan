"""Canonical names are not arbitrary business JSON keys named name."""
import copy

import pytest

from packages.jobs import final_contracts as fc
from tests.test_job_final_contracts import DEFINITION_FIXTURES, event_envelope, object_document


@pytest.mark.parametrize('location', ['arguments', 'literal', 'schema-default',
    'action-config', 'action-schema', 'creator-config', 'template-payload'])
def test_business_json_blank_name_is_validated_only_by_its_schema(location):
    kind = 'job'
    value = DEFINITION_FIXTURES[kind]()
    payload = {'name': ' ', 'nested': [{'name': '\t\n', 'jobDefinition': {'name': ' '}}]}
    if location == 'arguments':
        value['arguments'].update(copy.deepcopy(payload))
        value['inputSchema']['properties'].update(name={'type': 'string'}, nested={'type': 'array'})
    elif location == 'literal':
        value['steps'][0]['arguments']['argv'] = {'from': 'literal', 'value': [payload]}
    elif location == 'schema-default':
        value['inputSchema']['default'] = payload
    elif location == 'action-config':
        kind = 'action'
        value = DEFINITION_FIXTURES[kind]()
        value['execution']['config'] = payload
    elif location == 'action-schema':
        kind = 'action'
        value = DEFINITION_FIXTURES[kind]()
        value['inputSchema']['default'] = payload
    elif location == 'creator-config':
        kind = 'event_creator'
        value = DEFINITION_FIXTURES[kind]()
        value['config'] = payload
    else:
        kind = 'template'
        value = DEFINITION_FIXTURES[kind]()
        value['jobDefinition']['arguments'].update(payload)
        value['jobDefinition']['inputSchema']['properties'].update(
            name={'type': 'string'}, nested={'type': 'array'})
        value['composition']['creators'][0]['definition']['config'] = payload
    original = copy.deepcopy(value)
    normalized = fc.validate_definition(kind, value)
    assert value == original
    assert fc.validate_object(kind, object_document(kind, normalized))['name'] == original['name']


def test_event_payload_business_name_is_not_a_canonical_name():
    event = event_envelope(payload={'name': ' ', 'jobDefinition': {'name': '\t'}})
    assert fc.validate_event_publish(event)['payload'] == event['payload']


@pytest.mark.parametrize('kind', fc.DEFINITION_KINDS)
@pytest.mark.parametrize('name', ['English name', '中文名称', '  中 English  '])
def test_canonical_definition_and_document_names_keep_legal_original_text(kind, name):
    value = DEFINITION_FIXTURES[kind](name=name)
    assert fc.normalize_definition(kind, value)['name'] == name
    normalized = fc.validate_definition(kind, value)
    assert normalized['name'] == name
    assert fc.validate_object(kind, object_document(kind, normalized))['name'] == name


@pytest.mark.parametrize('kind', fc.DEFINITION_KINDS)
@pytest.mark.parametrize('name', [' ', '\t\n'])
def test_canonical_definition_and_document_names_still_reject_blank(kind, name):
    value = DEFINITION_FIXTURES[kind](name=name)
    with pytest.raises(fc.ContractError, match='non-whitespace'):
        fc.normalize_definition(kind, value)
    with pytest.raises(fc.ContractError, match='non-whitespace'):
        fc.validate_definition(kind, value)
    with pytest.raises(fc.ContractError, match='non-whitespace'):
        fc.validate_object(kind, object_document(kind, value))


@pytest.mark.parametrize('location', ['jobDefinition', 'creator', 'trigger'])
@pytest.mark.parametrize('name', [' ', '\t\n', '  合法 template  '])
def test_template_embedded_canonical_names_have_the_same_scope(location, name):
    value = DEFINITION_FIXTURES['template']()
    target = (value['jobDefinition'] if location == 'jobDefinition' else
        value['composition']['creators' if location == 'creator' else 'triggers'][0]['definition'])
    target['name'] = name
    if not name.strip():
        with pytest.raises(fc.ContractError, match='non-whitespace'):
            fc.normalize_definition('template', value)
        with pytest.raises(fc.ContractError, match='non-whitespace'):
            fc.validate_object('template', object_document('template', value))
    else:
        normalized = fc.validate_definition('template', value)
        assert normalized == fc.normalize_definition('template', value)
        document = fc.validate_object('template', object_document('template', normalized))
        for result in (normalized, document):
            embedded = (result['jobDefinition'] if location == 'jobDefinition' else
                result['composition']['creators' if location == 'creator' else 'triggers'][0]['definition'])
            assert embedded['name'] == name
        assert target['name'] == name
