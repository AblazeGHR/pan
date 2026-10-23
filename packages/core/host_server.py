"""Uvicorn signal boundary for the Pan host, separate from Worker interrupts."""
import uvicorn
import asyncio
import os
import inspect
from contextlib import contextmanager
from packages.core import worker


def _finish_reset_transport(context, servers):
    """Finish the stdlib Proactor close tail interrupted by peer reset.

    Only our listener's already-closing, drained transport is eligible. Its
    protocol connection_lost callback already ran; never call it a second time.
    """
    if os.name != 'nt' or not isinstance(context.get('exception'), ConnectionResetError):
        return False
    from asyncio.proactor_events import _ProactorBasePipeTransport
    callback = getattr(context.get('handle'), '_callback', None)
    transport = getattr(callback, '__self__', None)
    if (getattr(callback, '__func__', None) is not _ProactorBasePipeTransport._call_connection_lost
            or not isinstance(transport, _ProactorBasePipeTransport)):
        return False
    traceback = context['exception'].__traceback__
    while traceback is not None and traceback.tb_next is not None:
        traceback = traceback.tb_next
    if (traceback is None or traceback.tb_frame.f_code is not callback.__func__.__code__
            or traceback.tb_frame.f_locals.get('self') is not transport):
        # A protocol callback may itself raise ConnectionResetError. That is
        # not proof that its callback completed; leave it to the prior handler.
        return False
    server = getattr(transport, '_server', None)
    sock = getattr(transport, '_sock', None)
    if (server is None or not any(server is owned for owned in servers) or sock is None
            or not transport.is_closing() or transport._called_connection_lost
            or transport._read_fut is not None or transport._write_fut is not None
            or transport.get_write_buffer_size()):
        return False
    # Python 3.12 tracks an active count and _detach() takes no argument;
    # newer versions track transports and require _detach(transport). Validate
    # the bound call before closing anything, never retry a failed detach.
    try:
        detach_signature = inspect.signature(server._detach)
        try:
            detach_signature.bind(transport)
            detach_args = (transport,)
        except TypeError:
            detach_signature.bind()
            detach_args = ()
    except (TypeError, ValueError):
        return False
    # The original callback has already delivered protocol.connection_lost.
    # Its socket.shutdown raised before the following unconditional close tail.
    sock.close()
    transport._sock = None
    server._detach(*detach_args)
    transport._server = None
    transport._called_connection_lost = True
    return True


class PanServer(uvicorn.Server):
    @contextmanager
    def _reset_cleanup(self, loop):
        previous = loop.get_exception_handler()
        def handler(active_loop, context):
            try:
                recovered = _finish_reset_transport(context, getattr(self, 'servers', ()))
            except Exception:
                recovered = False  # Report the original failure through its existing handler.
            if recovered:
                from packages.core.runtime_diagnostics import runtime
                runtime.record('runtime.peerResetCloseRecovered')
                return
            if previous is not None:
                previous(active_loop, context)
            else:
                active_loop.default_exception_handler(context)
        loop.set_exception_handler(handler)
        try:
            yield
        finally:
            if loop.get_exception_handler() is handler:
                loop.set_exception_handler(previous)

    async def serve(self, sockets=None):
        from packages.core.logging_setup import _resolve_log_path
        from packages.core.config import load_config
        from packages.core.runtime_diagnostics import runtime
        directory = _resolve_log_path(load_config().get("logging", {})).parent
        with self._reset_cleanup(asyncio.get_running_loop()):
            async with runtime.monitor(directory):
                return await super().serve(sockets=sockets)

    def handle_exit(self, sig, frame):
        worker.begin_host_exit()
        super().handle_exit(sig, frame)
