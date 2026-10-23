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
        if len(canonical(value).encode('utf-8')) > 65536:
            raise InteractionError('capacity', 'Interaction payload exceeds 64 KiB')
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
    return value
