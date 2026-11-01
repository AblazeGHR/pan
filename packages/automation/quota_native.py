"""Thin quota composition and business-result mapping over canonical execution.

No execution receipts, schedule reservations, or recovery state live here.
"""
import copy
from datetime import datetime, timezone

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import object_ref
from packages.jobs.final_runtime.ledger import principal
from packages.plugins.lifecycle import io
from packages.scheduler.providers import instant, time_definition
from .quota_policy import CONTRACT_VERSION, Unavailable, _LOCK, fingerprint

ACTION = 'system_quota_query_v1'
OPERATION = 'quota-query.v1'


def action_definition():
    return {'name': 'Quota query', 'inputSchema': {'type': 'object',
        'properties': {'policy': {'type': 'object'}, 'compositionKey': {'type': 'string'}},
        'required': ['policy', 'compositionKey'], 'additionalProperties': False},
        'outputSchema': {'type': 'object'},
        'providerRef': {'id': 'pan.quota', 'generation': 1, 'releaseDigest': fc.digest(OPERATION)},
        'execution': {'operation': OPERATION, 'config': {}}, 'effectClass': 'read',
        'requiredCapabilities': [], 'completionContracts': [{'id': 'quota-observed',
            'schema': {'type': 'object'}, 'terminalProofSchema': {'type': 'object'}}],
        'defaultCompletion': 'quota-observed', 'cancel': {'supported': False},
        'reconcile': {'supported': False},
        'retry': {'idempotent': False, 'sameKeySupported': True, 'requiresProof': True}, 'system': True}


class NativeQuota:
    def __init__(self, policy, service):
        self.policy, self.service = policy, service

    def key(self, state, purpose, actor):
        return 'quota:' + fingerprint([self.service.profile_id, principal(actor), state['epoch'], purpose])

    def lookup(self, state, purpose, actor):
        try:
            return self.service.operation_for_key('create', self.key(state, purpose, actor), actor=actor)
        except KeyError:
            return None

    def remember(self, value, *, replace=False):
        # Repair a lost policy reference from the canonical operation receipt.
        # This index is never used as an execution or scheduling checkpoint.
        def update(s):
            if s['mode'] != 'automatic' or s['epoch'] != value['quotaPolicy']['epoch']:
                raise ValueError('Quota policy changed during admission')
            purpose = value['quotaPolicy']['purpose']
            if replace or s['jobs'].get(purpose) in (None, value['jobId']):
                s['jobs'][purpose] = value['jobId']
            s.setdefault('nativeJobs', {})[value['jobId']] = value['compositionKey']
        self.policy.store.change(update)

    def ensure(self, state, purpose, at, account=None, wake_target=None, actor=None):
        actor = actor or state.get('nativeAuthority')
        if not actor:
            raise Unavailable('Quota admission authority unavailable; configure policy again')
        with _LOCK:
            latest = self.policy.store.read()
            if (self.policy.closed or latest['mode'] != 'automatic'
                    or latest['epoch'] != state['epoch'] or account in latest['disabledAccountIds']):
                raise ValueError('Quota policy changed during admission')
            identity = {'contractVersion': CONTRACT_VERSION, 'epoch': state['epoch'], 'purpose': purpose,
                'maSessionId': state['maSessionId'], 'expectedAccountId': account, 'wakeTarget': wake_target,
                'acceptedRevision': state['revision']}
            prior = self.lookup(state, purpose, actor)
            if prior:
                value = prior['result']
                expected = {k: v for k, v in identity.items() if k != 'acceptedRevision'}
                actual = {k: v for k, v in value['quotaPolicy'].items() if k != 'acceptedRevision'}
                if actual != expected:
                    raise ValueError('Quota query Job identity conflict')
                self.remember(value)
                return copy.deepcopy(value)
            result = self.service.admit_quota(self.key(state, purpose, actor), identity, at, actor=actor)
            value = result['result']
            # References only. Rules/checkpoints/authorization remain canonical.
            self.remember(value, replace=True)
            self.cancel_obsolete()
            return copy.deepcopy(value)

    def graph(self, policy, at, action, composition_key):
        # A reset may already be due when its business command is dispatched.
        # Map fire_now to the next host tick, rather than an expired once rule
        # which the canonical Scheduler correctly excludes on creation.
        scheduled = self.policy.clock() if at is None else max(at, float(instant(self.service.creators.clock())) + 1)
        point = datetime.fromtimestamp(scheduled, timezone.utc).isoformat()
        rule = ({'ruleId': 'query', 'type': 'interval', 'seconds': '60', 'anchor': point,
                 'advanceMode': 'grid'} if at is None else {'ruleId': 'query', 'type': 'once', 'at': point})
        return {'job': {'name': 'Quota ' + policy['purpose'],
            'steps': [{'stepId': 'query', 'actionRef': object_ref('action', action),
                'arguments': {'policy': {'from': 'literal', 'value': policy},
                    'compositionKey': {'from': 'literal', 'value': composition_key}},
                'completion': {'contract': 'quota-observed'}}], 'retention': {'policyRef': 'quota-policy'},
            'overlap': {'mode': 'forbid', 'maxActive': 1, 'maxPending': 0},
            **({'runLimit': {'maxRuns': 1, 'counterDomain': 'run-admission'}}
               if policy['purpose'] != 'monitor95' else {})},
            'creators': [{'mode': 'create', 'key': 'clock', 'definition': time_definition('Quota query',
                {'rules': [rule], 'misfirePolicy': 'fire_now', 'graceSeconds': '1', 'pausePolicy': 'advance'})}],
            'triggers': [{'key': 'query', 'targetJobKey': 'job', 'creatorKeys': ['clock'],
                'definition': {'name': 'Quota query', 'eventTypes': ['scheduler.time.v2'],
                    'creatorSelector': {'mode': 'ids', 'ids': []}}}]}

    def disable(self, composed, reason, actor=None):
        svc = self.service
        actor = actor or composed['authorizationEvidence']['authority']
        ids = composed['objectIds']
        for kind, oid in [('event_creator', ids['creators']['clock']),
                          ('trigger', ids['triggers']['query']), ('job', ids['job'])]:
            item = svc.get(kind, oid, actor=actor)
            if item['state'] != 'enabled':
                continue
            svc.control(kind, oid, {'operation': 'disable', 'expectedRevision': item.get('configRevision', item.get('revision')),
                'expectedStateRevision': item['stateRevision'], 'parameters': {},
                'requestKey': 'quota-disable:' + fc.digest([oid, reason, item['stateRevision']])}, actor=actor)

    def cancel_obsolete(self, actor=None):
        state = self.policy.store.read()
        references = dict(state.get('nativeJobs', {}))
        # A canonical commit can survive a lost policy-index write. Discover
        # only this adapter's graphs through the existing authenticated reader.
        reader = actor or state.get('nativeAuthority')
        if reader:
            cursor = None
            while True:
                page = self.service.list('job', actor=reader, cursor=cursor)
                for job in page['items']:
                    steps = job['steps']
                    if len(steps) == 1 and steps[0]['actionRef']['id'] == ACTION:
                        binding = steps[0]['arguments'].get('compositionKey', {})
                        if binding.get('from') == 'literal':
                            references[job['id']] = binding['value']
                cursor = page['nextCursor']
                if cursor is None:
                    break
        for jid, key in references.items():
            composed = self.service.creators.composition_for_key(key)
            if not composed or composed['objectIds']['job'] != jid:
                continue
            identity = composed['desiredGraph']['job']['steps'][0]['arguments']['policy']['value']
            if (state['mode'] != 'automatic' or identity['epoch'] != state['epoch']
                    or (state['jobs'].get(identity['purpose']) is not None
                        and state['jobs'][identity['purpose']] != jid)
                    or identity['expectedAccountId'] in state['disabledAccountIds']):
                self.disable(composed, 'obsolete', actor)

    async def query(self, arguments, *, effect_key):
        identity = arguments['policy']
        # The canonical driver already admitted the original Attempt and checked
        # its frozen authority. Reads/recovery never call this handler again.
        state = await io(self.policy.store.read)
        if (self.policy.closed or state['mode'] != 'automatic' or state['epoch'] != identity['epoch']
                or identity.get('expectedAccountId') in state['disabledAccountIds']):
            return {'status': 'rejected', 'code': 'quota_policy_changed', 'quotaStatus': 'unknown'}
        composed = await io(self.service.creators.composition_for_key, arguments['compositionKey'])
        if (composed is None
                or composed['desiredGraph']['job']['steps'][0]['arguments']['policy']['value'] != identity):
            raise Unavailable('Quota canonical reference missing')
        snapshot = await self.policy.query()
        result = await self.policy.native_result(composed['objectIds']['job'], identity, snapshot)
        if result.get('stop'):
            await io(self.disable, composed, result['reason'])
        return {'status': 'succeeded' if snapshot['status'] == 'known' and not result.get('unknown') else 'unknown',
            'code': 'quota_observed' if snapshot['status'] == 'known' else 'quota_provider_unavailable',
            'quotaStatus': 'unknown' if result.get('unknown') else snapshot['status'],
            'thresholdReached': result['thresholdReached'], 'reason': result['reason']}
