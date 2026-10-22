"""Pure, fail-closed identity projection for the trusted quota host adapter."""
import math

LIVE = {'running', 'idle', 'queued', 'error'}
WORKER_KEYS = ('runtimeEpoch', 'sessionId', 'workerId', 'generation', 'processIdentity', 'adapter', 'accountId')


def identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and value == value.strip()


def valid_inventory(value, now):
    if not isinstance(value, dict) or not identifier(value.get('runtimeEpoch')):
        return False
    at = value.get('observedAt')
    workers = value.get('workers')
    return (type(at) in (int, float) and math.isfinite(at) and 0 <= now-at <= 5
            and isinstance(workers, list) and len(workers) <= 256
            and all(isinstance(w, dict) for w in workers)
            and len({w.get('sessionId') for w in workers if isinstance(w.get('sessionId'), str)}) == len(workers))


def unfinished(task):
    if (not isinstance(task, dict) or task.get('state') != 'unfinished'
            or not identifier(task.get('taskId')) or type(task.get('executionSeq')) is not int
            or task['executionSeq'] < 1):
        return None
    return {k: task[k] for k in ('taskId', 'executionSeq', 'state')}


def worker(value, epoch):
    if not isinstance(value, dict) or not identifier(epoch):
        return None
    process = value.get('processIdentity')
    if (value.get('runtimeEpoch', epoch) != epoch or value.get('alive') is not True
            or not isinstance(value.get('status'), str) or value['status'] not in LIVE
            or not all(identifier(value.get(k)) for k in ('sessionId', 'workerId', 'adapter', 'accountId'))
            or type(value.get('generation')) is not int or value['generation'] < 0
            or not isinstance(process, dict) or type(process.get('pid')) is not int
            or process['pid'] <= 0 or not identifier(process.get('birthToken'))):
        return None
    result = {k: value[k] for k in ('sessionId', 'workerId', 'generation', 'adapter', 'accountId', 'status')}
    result.update(runtimeEpoch=epoch, alive=True,
                  processIdentity={k: process[k] for k in ('pid', 'birthToken')},
                  role=value.get('role') if value.get('role') in ('ma', 'ta') else None,
                  task=unfinished(value.get('task')))
    return result


def frozen(value):
    return worker(value, value.get('runtimeEpoch')) if isinstance(value, dict) else None


def same_worker(left, right):
    return all(left.get(k) == right.get(k) for k in WORKER_KEYS)


def current(inventory, target):
    if not frozen(target) or inventory.get('runtimeEpoch') != target['runtimeEpoch']:
        return None
    for raw in inventory['workers']:
        found = worker(raw, inventory['runtimeEpoch'])
        if found and same_worker(found, target):
            return found
    return None


def original_eligible(original):
    """Eligibility at freeze time never includes idle or an inferred task."""
    return (frozen(original) is not None and original.get('role') in ('ta', 'ma')
            and original['status'] == 'running' and unfinished(original.get('task')) is not None)


def _same_task(original, current):
    return (all(original[k] == current[k] for k in ('runtimeEpoch', 'sessionId', 'adapter', 'role'))
            and unfinished(current.get('task')) == unfinished(original['task']))


def wake_eligible(original, current):
    """No restart proof: only the same running process and unfinished execution."""
    return (original_eligible(original) and frozen(current) is not None
            and same_worker(original, current) and current['status'] == 'running'
            and _same_task(original, current))


def recovery_eligible(original, restart, current, expected_account):
    """Allow idle only through a persisted confirmed restart's exact successor.

    The caller resolves this record from its own ledger and checks policy epoch /
    configuration revision. A running successor needs the same proof as an idle one.
    """
    if not original_eligible(original) or not isinstance(restart, dict) or frozen(current) is None:
        return False
    args = restart.get('arguments') or {}
    previous, successor = args.get('worker'), restart.get('successor')
    return (restart.get('contractVersion') == 2 and restart.get('status') == 'completed'
            and identifier(restart.get('id'))
            and restart.get('operation') == ('restart_worker' if original['role'] == 'ta' else 'restart_ma')
            and frozen(previous) is not None and same_worker(original, previous)
            and original_eligible(previous) and _same_task(original, previous)
            and frozen(successor) is not None and _same_task(original, successor)
            and successor['generation'] > original['generation']
            and successor['processIdentity']['birthToken'] != original['processIdentity']['birthToken']
            and args.get('runtimeEpoch') == original['runtimeEpoch']
            and identifier(expected_account) and args.get('expectedAccountId') == expected_account
            and successor['accountId'] == current['accountId'] == expected_account
            and same_worker(successor, current) and _same_task(original, current)
            and successor['status'] in ('running', 'idle') and current['status'] in ('running', 'idle'))
