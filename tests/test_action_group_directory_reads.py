"""Read-only directory contracts for the unified Action/Group UI.

P7 adds two reads that were missing from the canonical surface:

* a Group's committed release history (bounded, newest first),
* the explicit ``legacy_action_binding`` projection written by Group adoption.

Both are read-only: they never publish, adopt or rewrite a release, and they
reuse the existing authorization and immutable-release semantics.
"""
import base64
import json

from packages.actions.groups import Groups
from tests.test_action_unification_p1 import action_http
from tests.test_action_unification_p3 import USER, install, put


def apply_group_upgrade(groups, key='apply'):
    preview = groups.preview({'scope': 'group', 'groupId': 'tools'}, actor=USER)
    return groups.apply({
        'selection': preview['selection'],
        'previewDigest': preview['previewDigest'],
        'targets': [{'groupId': item['groupId'], 'expectedRevision': item['expectedRevision']}
                    for item in preview['items']],
        'requestKey': key,
    }, actor=USER)


def test_group_release_history_is_newest_first_and_matches_single_read(action_http):
    client, service = action_http
    groups = Groups(service)
    first_ref = install(service)
    put(groups, first_ref)
    first = groups.publish('tools', 1, 'publish-1', actor=USER)['result']['release']
    second_ref = install(service, command='echo second')
    apply_group_upgrade(groups)
    second = groups.publish('tools', 3, 'publish-2', actor=USER)['result']['release']

    response = client.get('/api/action-groups/tools/releases')
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {'items', 'nextCursor'}
    assert [item['version'] for item in body['items']] == [second['version'], first['version']]
    assert body['items'][1]['tools'][0]['actionRef'] == first_ref
    assert body['items'][0]['tools'][0]['actionRef'] == second_ref
    assert body['nextCursor'] is None

    # The listed item is the same immutable document the single read returns.
    single = client.get(f"/api/action-groups/releases/{first['version']}")
    assert single.status_code == 200
    assert single.json() == body['items'][1]
    # Reading history never creates or moves a release.
    assert groups.list(actor=USER)['items'][0]['release'] == second['version']


def test_group_release_history_is_group_scoped_and_bounded(action_http):
    client, service = action_http
    groups = Groups(service)
    put(groups, install(service))
    for index, expected in enumerate((1, 2, 3), start=1):
        groups.publish('tools', expected, f'publish-{index}', actor=USER)
    put(groups, install(service, command='echo other'), 'other')
    other = groups.publish('other', 1, 'publish-other', actor=USER)['result']['release']

    page = client.get('/api/action-groups/tools/releases?limit=2')
    assert page.status_code == 200
    body = page.json()
    assert len(body['items']) == 2
    assert body['nextCursor']
    follow = client.get(f"/api/action-groups/tools/releases?limit=2&cursor={body['nextCursor']}")
    assert follow.status_code == 200
    assert len(follow.json()['items']) == 1
    assert all(item['groupId'] == 'tools' for item in body['items'] + follow.json()['items'])
    assert other['version'] not in [item['version'] for item in body['items']]

    # A cursor from another Group must not page this Group.
    foreign = base64.urlsafe_b64encode(json.dumps(
        {'groupId': 'other', 'offset': 0}, separators=(',', ':')).encode()).decode()
    cross = client.get(f'/api/action-groups/tools/releases?cursor={foreign}')
    assert cross.status_code == 400
    assert cross.json()['error']['code'] == 'invalid_schema'
    assert client.get('/api/action-groups/tools/releases?cursor=not-a-cursor').status_code == 400


def test_group_release_history_errors_keep_existing_semantics(action_http):
    client, _ = action_http
    assert client.get('/api/action-groups/missing/releases').status_code == 404
    unknown = client.get('/api/action-groups/tools/releases?limit=0')
    assert unknown.status_code == 400
    assert unknown.json()['error']['code'] == 'invalid_schema'
    assert client.get('/api/action-groups/tools/releases?limit=101').status_code == 400
    assert client.get('/api/action-groups/tools/releases?other=1').status_code == 400


def test_empty_history_for_an_unpublished_group_is_not_an_error(action_http):
    client, service = action_http
    groups = Groups(service)
    put(groups, install(service))
    response = client.get('/api/action-groups/tools/releases')
    assert response.status_code == 200
    assert response.json() == {'items': [], 'nextCursor': None}


def test_legacy_action_bindings_expose_only_explicit_ids(action_http):
    client, service = action_http
    with service.transaction() as db:
        Groups.save(db, 'legacy_action_binding', 'k-1', {
            'sourceRoot': 'C:/owned/source', 'legacyId': 'old_alias', 'actionId': 'action_1',
            'raw': {'id': 'old_alias', 'execution': {'kind': 'command', 'executable': 'secret'}},
            'deleted': False})
        Groups.save(db, 'legacy_action_binding', 'k-2', {
            'sourceRoot': 'C:/owned/source', 'legacyId': 'gone_alias', 'actionId': 'action_2',
            'raw': {'id': 'gone_alias'}, 'deleted': True})

    response = client.get('/api/action-groups/legacy-action-bindings')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['nextCursor'] is None
    assert body['items'] == [
        {'legacyId': 'gone_alias', 'actionId': 'action_2', 'sourceRoot': 'C:/owned/source', 'deleted': True},
        {'legacyId': 'old_alias', 'actionId': 'action_1', 'sourceRoot': 'C:/owned/source', 'deleted': False},
    ]
    # The projection is identifiers only; the stored legacy definition is not
    # republished through this read.
    assert all(set(item) == {'legacyId', 'actionId', 'sourceRoot', 'deleted'} for item in body['items'])
    # The singular alias route keeps its distinct contract: it reads the
    # explicitly registered alias table, not adoption bindings.
    assert client.get('/api/jobs/actions/aliases/old_alias').status_code == 404


def test_legacy_action_bindings_paging_and_bounds(action_http):
    client, service = action_http
    with service.transaction() as db:
        for index in range(3):
            Groups.save(db, 'legacy_action_binding', f'k-{index}', {
                'sourceRoot': 'C:/owned/source', 'legacyId': f'alias_{index}',
                'actionId': f'action_{index}', 'raw': {}, 'deleted': False})
    first = client.get('/api/action-groups/legacy-action-bindings?limit=2')
    assert first.status_code == 200
    assert [item['legacyId'] for item in first.json()['items']] == ['alias_0', 'alias_1']
    assert first.json()['nextCursor']
    second = client.get(
        f"/api/action-groups/legacy-action-bindings?limit=2&cursor={first.json()['nextCursor']}")
    assert [item['legacyId'] for item in second.json()['items']] == ['alias_2']
    assert client.get('/api/action-groups/legacy-action-bindings?limit=0').status_code == 400
    assert client.get('/api/action-groups/legacy-action-bindings?unknown=1').status_code == 400


def test_directory_reads_require_the_action_read_grant(action_http):
    client, service = action_http
    groups = Groups(service)
    put(groups, install(service))
    groups.publish('tools', 1, 'publish-1', actor=USER)
    assert client.get('/api/action-groups/tools/releases',
                      headers={'Authorization': 'Bearer not-a-user'}).status_code == 401
    assert client.get('/api/action-groups/legacy-action-bindings',
                      headers={'Authorization': 'Bearer not-a-user'}).status_code == 401
