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


def wake_eligible(original, successor):
    return (frozen(original) is not None and frozen(successor) is not None
            and all(original[k] == successor[k] for k in ('runtimeEpoch', 'sessionId', 'adapter'))
            and successor['generation'] >= original['generation']
            and original.get('role') in ('ta', 'ma') and successor.get('role') == original['role']
            and original['status'] == successor['status'] == 'running'
            and unfinished(original.get('task')) is not None
            and unfinished(successor.get('task')) == unfinished(original['task']))
