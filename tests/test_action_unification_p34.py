"""Combined Group-to-Job provenance contract."""
import asyncio
from types import SimpleNamespace

from packages.actions.group_catalog import invoke
from packages.actions.groups import Groups
from tests.test_action_unification_p4 import h, USER, grant, install, job_def


def test_group_job_creation_preserves_trusted_release_and_original_receipt(h):
    ref = install(h)
    groups = Groups(h.service)
    groups.put({'id': 'creators', 'name': 'creators', 'tools': [{'alias': 'create', 'actionRef': ref}]},
               0, 'p34-group', actor=USER)
    release = groups.publish('creators', 1, 'p34-build', actor=USER)['result']['release']
    grant(h, action={'read': True, 'invoke': True}, job={'read': True, 'create': True})
    descriptor = {'action_group_release': release['version'], 'plugin': 'action-group-creators',
                  'source_name': 'action-group-creators.create'}
    arguments = {'arguments': {'jobDefinition': job_def(h)}, 'requestKey': 'p34-create'}
    runtime = SimpleNamespace(group_service=lambda: h.service)
    first = asyncio.run(invoke(runtime, descriptor, arguments))['structuredContent']
    replay = asyncio.run(invoke(runtime, descriptor, arguments))['structuredContent']
    assert replay['operationId'] == first['operationId']
    assert replay['result']['jobId'] == first['result']['jobId']
    assert first['result']['provenance']['source']['groupRelease'] == release['version']
    assert len(h.service.list('job', actor=USER)['items']) == 1
