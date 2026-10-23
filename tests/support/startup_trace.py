"""Owned startup diagnostics before imports; never changes the ready deadline."""
import faulthandler
import os
from pathlib import Path
import sys
import time

_output = None


def arm():
    global _output
    if _output is not None:
        return
    supplied = os.environ.get('PAN_E2E_DATA_ROOT')
    if not supplied and '--data-root' in sys.argv:
        supplied = sys.argv[sys.argv.index('--data-root') + 1]
    if not supplied:
        return  # Never infer a shared/default profile for diagnostics.
    root = Path(supplied).resolve()
    root.mkdir(parents=True, exist_ok=True)
    _output = (root / 'startup-stacks.txt').open('a', encoding='utf-8')
    _output.write(f'pid={os.getpid()} startup armed at {time.time()}\n')
    _output.flush()
    faulthandler.dump_traceback_later(15, file=_output)


class StartupTrace:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def observed(message):
            if message['type'] == 'lifespan.startup.complete':
                faulthandler.cancel_dump_traceback_later()
                if _output is not None:
                    _output.write(f'pid={os.getpid()} startup complete at {time.time()}\n')
                    _output.flush()
            await send(message)
        await self.app(scope, receive, observed if scope['type'] == 'lifespan' else send)
