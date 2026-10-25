"""Strict, versioned Interaction Card wire validation; no runtime policy inferred."""
import json
from pathlib import Path
import jsonschema
from packages.core.agent_inbox import canonical, identity

SCHEMA = json.loads(Path(__file__).with_name('schema.json').read_text(encoding='utf-8-sig'))


class InteractionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def validate(kind, value):
    try:
        # Keep the v1 byte limit; v2 permits twenty full Unicode text answers.
        def mapping(item):
            return item if isinstance(item, dict) else {}
        obj = mapping(value)
        v2 = (obj.get('schemaVersion') == 2
              or mapping(obj.get('card')).get('schemaVersion') == 2
              or 'answers' in obj
              or 'answers' in mapping(mapping(obj.get('payload')).get('response')))
        if len(canonical(value).encode('utf-8')) > (2_000_000 if v2 else 65536):
            raise InteractionError('capacity', 'Interaction payload exceeds capacity')
        jsonschema.Draft202012Validator({'$ref': '#/$defs/' + kind, '$defs': SCHEMA['$defs']}).validate(value)
    except (jsonschema.ValidationError, ValueError, TypeError) as exc:
        if isinstance(exc, InteractionError):
            raise
        raise InteractionError('invalid', 'Invalid ' + kind + ' payload') from exc
    if kind in {'Create', 'Definition'}:
        definition = value['card'] if kind == 'Create' else value
        ids = [choice['id'] for choice in definition.get('choices', [])]
        if len(ids) != len(set(ids)):
            raise InteractionError('invalid', 'Choice IDs must be unique')
        questions = definition.get('questions', [])
        if len({q['id'] for q in questions}) != len(questions):
            raise InteractionError('invalid', 'Question IDs must be unique')
        for question in questions:
            ids = [c['id'] for c in question.get('choices', [])]
            if len(ids) != len(set(ids)):
                raise InteractionError('invalid', 'Choice IDs must be unique within a question')
    return value


def validate_answers(definition, answers):
    """Validate the entire submission before any response or execution is persisted."""
    questions = {q['id']: q for q in definition['questions']}
    if set(answers) - questions.keys():
        raise InteractionError('invalid', 'Unknown question ID')
    for qid, question in questions.items():
        answer = answers.get(qid)
        if answer is None:
            if question.get('required', True):
                raise InteractionError('invalid', f'{qid}: answer or rejection required')
            continue
        if answer.get('rejected') is True:
            continue
        field = {'text': 'text', 'single_choice': 'choiceId', 'multi_choice': 'choiceIds'}[question['type']]
        if field not in answer:
            raise InteractionError('invalid', f'{qid}: incorrect answer type')
        if field == 'text':
            if not answer['text'].strip():
                raise InteractionError('invalid', f'{qid}: text must not be blank')
        else:
            selected = [answer[field]] if field == 'choiceId' else answer[field]
            if set(selected) - {c['id'] for c in question['choices']}:
                raise InteractionError('invalid', f'{qid}: unknown choice')


def selected_choices(definition, data):
    """Yield (question ID, choice); v1 keeps its original unscoped key."""
    if definition.get('schemaVersion', 1) == 2:
        for q in definition['questions']:
            answer = data.get('answers', {}).get(q['id'], {})
            ids = answer.get('choiceIds', [answer.get('choiceId')])
            for choice in q.get('choices', []):
                if choice['id'] in ids:
                    yield q['id'], choice
    else:
        for choice in definition.get('choices', []):
            if choice['id'] == data.get('choiceId'):
                yield None, choice


def choice_binding(bindings, question_id, choice_id):
    return (bindings.get(question_id, {}) if question_id is not None else bindings).get(choice_id)
