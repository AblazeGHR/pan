"""Card-only, read-only proof of existing fixed Session Job targets.

This is not a graph executor or a general reference policy. The caller must
retain the result in a versioned Card binding and reverify it before admission.
"""
import copy
import json

from packages.jobs.final_service import definition, object_ref
from .contracts import InteractionError


PRODUCTS = {
    'product_session_message': ('session-message', 'sessionId'),
    'product_session_message_v2': ('session-message', 'sessionId'),
    'product_session_broadcast': ('session-broadcast', 'sessionIds'),
    'product_session_broadcast_v2': ('session-broadcast', 'sessionIds'),
    'product_assign_v2': ('assign', 'sessionId'),
}


def read_fixed_graph(host, trigger_id, *, expected_revision=None):
    """Read current graph/authority under the host's existing transaction.

    Every Step must use a known product Session release and literal targets.
    Unknown Actions or dynamic selectors cannot prove their complete scope.
    """
    from .trusted import digest
    try:
        with host.transaction(readonly=True) as db:
            trigger = host._get(db, 'trigger', trigger_id)
            if trigger['state'] != 'enabled' or 'pan.interaction.responded' not in trigger['eventTypes']:
                raise InteractionError('incomplete', 'Enabled interaction Trigger required')
            if expected_revision is not None and trigger['revision'] != expected_revision:
                raise InteractionError('stale', 'Trigger revision changed')
            if trigger.get('condition') is not None:
                raise InteractionError('incomplete', 'Card fixed-target admission cannot observe asynchronous conditions')
            job = host._get(db, 'job', trigger['targetJobId'])['canonical']
            if job['state'] != 'enabled':
                raise InteractionError('stale', 'Approved Job is unavailable')
            row = db.execute("SELECT document FROM final_authorities WHERE kind='trigger' AND id=?", (trigger_id,)).fetchone()
            if row is None:
                raise InteractionError('incomplete', 'Trigger authority unavailable')
            authority = json.loads(row[0])
            host._permit(authority, 'trigger', 'lifecycle', execution=True)
            host._permit(authority, 'job', 'execute', execution=True)
            actions, ids = [], []
            for step in job['steps']:
                ref = step['actionRef']
                action = host._get(db, 'action', ref['id'])
                product = PRODUCTS.get(action['id'])
                if (product is None or action['state'] != 'published' or not action['enabled']
                        or action.get('system') is not True or action['providerRef']['id'] != 'pan.typed'
                        or action['execution']['operation'] != product[0] or object_ref('action', action) != ref):
                    raise InteractionError('incomplete', 'Choice requires known immutable Session Actions')
                host._permit(authority, 'action', 'manageSystemActions', execution=True)
                target = step['arguments'].get(product[1], {})
                if target.get('from') != 'literal':
                    raise InteractionError('incomplete', 'Choice requires fixed literal Session targets')
                targets = target.get('value')
                targets = targets if product[1] == 'sessionIds' else [targets]
                if (not isinstance(targets, list) or not targets or len(targets) > 256
                        or any(not isinstance(s, str) or not s for s in targets)):
                    raise InteractionError('incomplete', 'Invalid fixed Session target set')
                ids.extend(targets)
                if len(set(ids)) > 256:
                    raise InteractionError('incomplete', 'Card target scope exceeds bound')
                if ref not in actions:
                    actions.append(copy.deepcopy(ref))
            if not actions:
                raise InteractionError('incomplete', 'Session Job has no effects')
            return {'triggerId': trigger_id, 'triggerRevision': trigger['revision'],
                    'triggerDigest': digest(trigger), 'jobId': job['id'],
                    'jobRevision': job['configRevision'], 'jobDigest': digest(definition('job', job)),
                    'actions': actions, 'authorityDigest': digest(authority),
                    'targetIds': list(dict.fromkeys(ids))}
    except InteractionError:
        raise
    except KeyError as exc:
        raise InteractionError('incomplete', 'Approved canonical graph unavailable') from exc
    except Exception as exc:
        raise InteractionError('forbidden', 'Canonical graph authority unavailable') from exc
