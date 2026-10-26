"""Read-only legacy composition evidence.

New graphs belong to scheduler.composition.CompositionCoordinator. Reading an
old operation never resumes it, creates a Job, or enables a time source.
"""
import copy

from .store import Conflict


class Links:
    def __init__(self, runtime, ledger, sources):
        self.ledger, self.sources = ledger, sources

    def job_pending(self, job_id):
        return any(op.get('jobId') == job_id and op['status'] not in {'complete', 'cancelled'}
                   for op in self.ledger.read().get('linkOperations', {}).values())

    def source_ready(self, key):
        return False  # Historical completion cannot authorize a source pass.

    def references(self, source_id):
        from packages.scheduler.time_contract import EVENT_TYPE
        refs = {row['triggerId']: {**row, 'name': 'Historical setup', 'enabled': False}
                for row in self.sources.references(source_id)}
        for item in self.ledger.read()['triggers'].values():
            if (EVENT_TYPE in item['eventTypes']
                    and item.get('eventFilter', {}).get('payload.scheduleId', source_id) == source_id):
                refs[item['id']] = {'triggerId': item['id'], 'name': item['name'],
                                   'jobId': item.get('targetJobId'), 'enabled': False}
        return copy.deepcopy(list(refs.values()))

    def operations(self):
        return [{key: copy.deepcopy(value) for key, value in op.items() if key not in {'request', 'config'}}
                for op in self.ledger.read().get('linkOperations', {}).values()
                if op['status'] not in {'complete', 'cancelled'}]

    @staticmethod
    def _retired(*args, **kwargs):
        raise Conflict('Legacy composition is read only; use canonical composition operations')

    reserve = checkpoint = quarantine = remove_owned = _retired

    async def compose(self, *args, **kwargs):
        self._retired()

    async def recover(self):
        self._retired()

    async def cancel(self, *args, **kwargs):
        self._retired()
