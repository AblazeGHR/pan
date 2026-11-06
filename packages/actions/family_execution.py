"""Identity-neutral adapters to existing family primitives.

The calling operation or Job Attempt owns admission, fences and checkpoints.
There is no persistence, recovery loop, scheduler or authority in this module.
"""
import copy

from .command_execution import CommandContext, OPERATIONS, argument_defaults, prepare
from .legacy_families import SYSTEM_OPERATIONS, system_plan, system_view

OPERATIONS_FAMILY = frozenset({'api.get', 'session.pause-everything', *('legacy.' + op for op in SYSTEM_OPERATIONS)})


def prepare_family(action, arguments):
    operation = action['execution']['operation']
    if operation.startswith('legacy.'):
        system_view(action, True)
        value = system_plan(operation, argument_defaults(action['inputSchema'], arguments))
        if value['operation'] in OPERATIONS:
            value['commands'] = prepare(value['operation'], value['config'], value['arguments'])
        return value
    from .group_migration import legacy_action_view
    return legacy_action_view(action, {'id': 'invocation', 'output_format': 'json'}, True)


async def execute_family(service, operation, prepared, arguments, context, *, event=None, attempt=None):
    if operation.startswith('legacy.'):
        if prepared['operation'] in OPERATIONS:
            return await service.executor.execute_commands(prepared_commands=prepared['commands'], frozen_plan=prepared,
                context=CommandContext(context.identity, context.boundary, lambda rows: context.checkpoint({'commands': rows})))
        result = await service.executor.execute_provider(frozen_plan=prepared, attempt=attempt,
            context=CommandContext(context.identity, context.boundary, lambda rows: context.checkpoint({'providerReceipts': rows})))
        return {'outcome': result['outcome'], 'receipt': {
            'status': {'succeeded': 'completed', 'rejected': 'failed', 'partial': 'partial', 'unknown': 'uncertain'}[result['outcome']],
            'providerReceipt': result, 'output': result['receipt']}}
    async def before_effect():
        with context.boundary():
            pass
    try:
        if operation == 'api.get':
            from .provider import execute
            output = await execute(prepared, arguments, before_effect=before_effect)
        else:
            from .executor import execute_dynamic
            async def progress(**fields):
                combined = {**progress.checkpoint, **copy.deepcopy(fields)}
                context.checkpoint({'checkpoint': combined})
                progress.checkpoint = combined
            progress.checkpoint = {'id': context.identity.get('operationId', context.identity.get('runId'))}
            progress.before_effect = before_effect
            output = await execute_dynamic(prepared, arguments, event or {}, progress, service.session_pause)
    except Exception as exc:
        return {'outcome': 'unknown', 'receipt': {'status': 'uncertain', 'error': str(exc) or type(exc).__name__}}
    status = 'completed' if output.get('ok', True) else 'failed'
    return {'outcome': 'succeeded' if status == 'completed' else 'rejected', 'receipt': {'status': status, 'output': output}}
