"""Closed, bounded Session predicate language; no expressions or caller authority."""

MAX_DEPTH = 8
MAX_NODES = 64
MAX_VALUES = 128
MAX_TEXT = 512
MAX_CANDIDATES = 2048
MAX_RESULTS = 256
FIELDS = {'id': 'string', 'name': 'string', 'adapter': 'string', 'managedBy': 'nullable_string',
          'readonlySession': 'boolean', 'pinned': 'boolean', 'workspaceIds': 'strings'}


def validate_action_query(raw):
    """Bound query recursion before the existing Action/draft deep copies."""
    if not isinstance(raw, dict):
        return
    execution = raw.get('execution')
    selector = execution.get('selector') if isinstance(execution, dict) else None
    if isinstance(selector, dict) and selector.get('mode') == 'query':
        validate_query(selector.get('query'))


def typed(value, kind):
    if kind == 'boolean':
        return type(value) is bool
    if kind == 'nullable_string' and value is None:
        return True
    if kind in {'string', 'nullable_string'}:
        return isinstance(value, str) and len(value) <= MAX_TEXT
    if kind == 'strings':
        return isinstance(value, list) and len(value) <= MAX_VALUES and all(typed(v, 'string') for v in value)
    return False


def validate_query(predicate):
    nodes = 0
    def visit(node, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise ValueError('Session query exceeds depth/node limit (8/64)')
        if not isinstance(node, dict):
            raise ValueError('Session query predicate must be an object')
        if set(node) in ({'and'}, {'or'}):
            children = next(iter(node.values()))
            if not isinstance(children, list) or not 1 <= len(children) <= MAX_NODES:
                raise ValueError('Session query and/or requires a nonempty bounded list')
            for child in children:
                visit(child, depth + 1)
            return
        if set(node) == {'not'}:
            visit(node['not'], depth + 1)
            return
        if set(node) != {'field', 'op', 'value'} or not isinstance(node['field'], str) or node['field'] not in FIELDS:
            raise ValueError('Unknown Session query field or predicate keys')
        kind, op, value = FIELDS[node['field']], node['op'], node['value']
        if op in ('eq', 'ne') and typed(value, kind):
            return
        if op == 'in' and kind != 'strings' and isinstance(value, list) and 1 <= len(value) <= MAX_VALUES and all(typed(v, kind) for v in value):
            return
        if op == 'contains' and (kind == 'strings' or node['field'] == 'name') and typed(value, 'string') and (kind == 'strings' or value):
            return
        raise ValueError('Invalid Session query operator/value for field ' + node['field'])
    visit(predicate, 1)
    return predicate


def matches(node, row):
    # Only invoked on a fully validated tree and a projected metadata snapshot.
    if 'and' in node:
        return all(matches(child, row) for child in node['and'])
    if 'or' in node:
        return any(matches(child, row) for child in node['or'])
    if 'not' in node:
        return not matches(node['not'], row)
    actual, value, op = row[node['field']], node['value'], node['op']
    if op == 'eq':
        return actual == value
    if op == 'ne':
        return actual != value
    if op == 'in':
        return actual in value
    return value in actual


def metadata_typed(value, kind):
    if kind == 'boolean':
        return type(value) is bool
    if kind == 'strings':
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if kind == 'nullable_string' and value is None:
        return True
    return isinstance(value, str)


def select(predicate, rows):
    validate_query(predicate)  # validates all branches, even ones short-circuited later
    by_id = {}
    count = 0
    for row in rows:
        count += 1
        if count > MAX_CANDIDATES:
            raise ValueError('Session query candidate limit exceeded (2048)')
        if not isinstance(row, dict) or any(key not in row for key in FIELDS):
            raise ValueError('Invalid Session query metadata snapshot')
        # Runtime metadata strings can exceed query literal length; only query
        # literals are length-limited. Reject wrong metadata types fail-closed.
        for key, kind in FIELDS.items():
            value = row[key]
            if not metadata_typed(value, kind):
                raise ValueError('Invalid Session query metadata field ' + key)
        if not row['id']:
            raise ValueError('Invalid Session ID in query snapshot')
        if matches(predicate, row):
            by_id[row['id']] = True
            if len(by_id) > MAX_RESULTS:
                raise ValueError('Session query result limit exceeded (256)')
    return {'ids': sorted(by_id), 'candidateCount': count}


def snapshot():
    from packages.core import session
    # Bounded list admission before any cold Session file hydration; history is
    # never requested. Shared pin order and effective root workspace are truth.
    sessions = session.list_all(load_history=False, limit=MAX_CANDIDATES)
    pinned = set(session.session_pin_state()['sessionIds'])
    return [dict(id=s.id, name=s.name, adapter=s.adapter, managedBy=s.managed_by,
                 readonlySession=s.readonly_session, pinned=s.id in pinned,
                 workspaceIds=session.effective_workspace_ids(s)) for s in sessions]


async def resolve(predicate):
    def read_and_select():
        validate_query(predicate)
        return select(predicate, snapshot())
    from packages.plugins.lifecycle import io
    return await io(read_and_select)


def predicate_schema():
    """Machine-readable grammar; depth/node bounds are additional semantic checks."""
    branches = []
    def leaf(field, op, schema):
        return {'type': 'object', 'additionalProperties': False,
                'required': ['field', 'op', 'value'], 'properties': {
                    'field': {'const': field}, 'op': {'enum': op}, 'value': schema}}
    for field, kind in FIELDS.items():
        scalar = ({'type': 'boolean'} if kind == 'boolean' else
                  {'type': ['string', 'null'], 'maxLength': MAX_TEXT} if kind == 'nullable_string' else
                  {'type': 'string', 'maxLength': MAX_TEXT})
        value = {'type': 'array', 'maxItems': MAX_VALUES, 'items': {'type': 'string', 'maxLength': MAX_TEXT}} if kind == 'strings' else scalar
        branches.append(leaf(field, ['eq', 'ne'], value))
        if kind != 'strings':
            branches.append(leaf(field, ['in'], {'type': 'array', 'minItems': 1, 'maxItems': MAX_VALUES, 'items': scalar}))
        if field in {'name', 'workspaceIds'}:
            branches.append(leaf(field, ['contains'], {'type': 'string', 'maxLength': MAX_TEXT, **({'minLength': 1} if field == 'name' else {})}))
    for op in ('and', 'or', 'not'):
        value = {'$ref': '#/$defs/predicate'}
        if op != 'not':
            value = {'type': 'array', 'minItems': 1, 'maxItems': MAX_NODES, 'items': value}
        branches.append({'type': 'object', 'additionalProperties': False, 'required': [op], 'properties': {op: value}})
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$ref': '#/$defs/predicate', '$defs': {'predicate': {'oneOf': branches}}}


def template():
    return {'item': {'name': 'Pause matching Sessions', 'description': 'Pause Everything for a frozen Session query result',
                    'enabled': False, 'effect': 'write_local', 'output_format': 'json',
                    'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                    'execution': {'kind': 'session_operation', 'operation': 'pause_everything',
                                  'selector': {'mode': 'query', 'query': {'field': 'name', 'op': 'eq', 'value': 'Choose a Session name'}}}},
            'queryFields': FIELDS.copy(), 'queryPredicateSchema': predicate_schema(),
            'limits': {'depth': MAX_DEPTH, 'nodes': MAX_NODES, 'values': MAX_VALUES, 'textLength': MAX_TEXT,
                       'candidates': MAX_CANDIDATES, 'results': MAX_RESULTS}}
