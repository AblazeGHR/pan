"""ACP stdio adapter over a narrowly scoped request transport.

The transport is responsible for authentication and server identity. This
adapter never receives or filters raw Pan events.
"""
import asyncio


class RecoveredTerminal(RuntimeError):
    def __init__(self, receipt):
        super().__init__('terminal confirmed; stream outcome unknown')
        self.receipt = receipt


class Backend:
    def __init__(self, request, *, poll_interval=0.1):
        self.request = request
        self.poll_interval = poll_interval
        self.contexts = set()
        self.uncertain = set()

    async def prepare(self, cwd, servers=None):
        args = {'cwd': cwd}
        if servers:
            args['servers'] = servers
        result = await self.request('prepare', args)
        handle = result['handle']
        self.contexts.add(handle)
        return handle

    async def prompt(self, context, blocks, turn_id, emit):
        self.uncertain.add(context)
        await self.request('submit', {'handle': context, 'turnId': turn_id, 'blocks': blocks})
        cursor = 0
        permissions_seen = set()
        while True:
            # Poll is a read at a fixed cursor; a lost response is safe to
            # retry. Submit, finish and cancel are deliberately never retried.
            for attempt in range(3):
                try:
                    result = await self.request('poll', {'handle': context, 'turnId': turn_id, 'cursor': cursor})
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(self.poll_interval)
                except RuntimeError:
                    # A restarted service can recover the exact durable turn,
                    # but lost stream contents cannot be declared delivered.
                    receipt = await self.request('recover', {'handle':context, 'turnId':turn_id})
                    if receipt.get('state') == 'terminal':
                        raise RecoveredTerminal(receipt)
                    raise
            for chunk in result['chunks']:
                await emit(chunk)
            for permission in result.get('permissions', []):
                permission_id = permission.get('id') if isinstance(permission, dict) else None
                if not isinstance(permission_id, str) or permission_id in permissions_seen:
                    continue
                permissions_seen.add(permission_id)
                decision = await emit({'permissionId': permission_id, 'command': permission.get('command')})
                await self.request('permission', {'handle': context, 'turnId': turn_id,
                                   'permissionId': permission_id,
                                   'decision': 'accept' if decision == 'accept' else 'decline'})
            cursor = result['cursor']
            if result['status'] is not None:
                try:
                    await self.request('finish', {'handle': context, 'turnId': turn_id, 'cursor': cursor})
                except Exception:
                    try:
                        receipt = await self.request('recover', {'handle':context, 'turnId':turn_id})
                    except Exception:
                        raise
                    if receipt.get('state') == 'terminal':
                        raise RecoveredTerminal(receipt)
                    raise RuntimeError('finish outcome unknown')
                self.uncertain.discard(context)
            if result['status'] == 'done':
                return 'end_turn'
            if result['status'] == 'cancelled':
                return 'cancelled'
            if result['status'] is not None:
                raise RuntimeError('turn failed or unconfirmed')
            await asyncio.sleep(self.poll_interval)

    async def cancel(self, context, turn_id):
        await self.request('cancel', {'handle': context, 'turnId': turn_id})

    async def disconnect(self):
        # Disconnect does not cancel or replay an uncertain turn.
        for handle in self.contexts:
            try:
                await self.request('abandon' if handle in self.uncertain else 'release', {'handle': handle})
            except Exception:
                pass
        self.contexts.clear()
