"""Owned host's exact Proactor peer-reset close tail; no real service."""
import asyncio
from types import SimpleNamespace
import types
import pytest
from asyncio.proactor_events import _ProactorBasePipeTransport
from packages.core.host_server import PanServer, _finish_reset_transport


def broken_close():
    calls = []
    class Socket:
        def fileno(self): return 1
        def shutdown(self, _): raise ConnectionResetError('peer reset')
        def close(self): calls.append('socket.close')
    class Server:
        def _detach(self, transport): calls.append('server.detach')
    transport = object.__new__(_ProactorBasePipeTransport)
    transport._sock = Socket()
    transport._server = Server()
    transport._protocol = SimpleNamespace(connection_lost=lambda exc: calls.append('protocol.lost'))
    transport._closing = True
    transport._called_connection_lost = False
    transport._read_fut = transport._write_fut = None
    transport._pending_write = 0
    transport._buffer = None
    try:
        transport._call_connection_lost(None)
    except ConnectionResetError as exc:
        # socket.shutdown is native in production: its reset ends at the
        # stdlib callback frame. Remove only the synthetic Socket method frame.
        trace = exc.__traceback__.tb_next
        exc.__traceback__ = types.TracebackType(None, trace.tb_frame, trace.tb_lasti, trace.tb_lineno)
        context = {'exception': exc, 'handle': SimpleNamespace(_callback=transport._call_connection_lost)}
    return transport, context, calls


def test_reset_close_detaches_once_and_preserves_original_protocol_callback():
    transport, context, calls = broken_close()
    assert calls == ['protocol.lost'] and transport._server is not None
    assert _finish_reset_transport(context, [transport._server])
    assert calls == ['protocol.lost', 'socket.close', 'server.detach']
    assert transport._called_connection_lost and transport._sock is transport._server is None
    assert not _finish_reset_transport(context, [])
    transport._call_connection_lost(None)
    assert calls == ['protocol.lost', 'socket.close', 'server.detach']


@pytest.mark.parametrize('boundary', ['foreign', 'write', 'read', 'not-closing', 'other-error'])
def test_reset_close_does_not_touch_other_ownership_or_io(boundary):
    transport, context, calls = broken_close()
    servers = [transport._server]
    if boundary == 'foreign': servers = [object()]
    elif boundary == 'write': transport._write_fut = object()
    elif boundary == 'read': transport._read_fut = object()
    elif boundary == 'not-closing': transport._closing = False
    else: context['exception'] = OSError('other')
    assert not _finish_reset_transport(context, servers)
    assert calls == ['protocol.lost']
    # Finish this synthetic transport explicitly, avoiding destructor warnings.
    transport._sock.close()
    transport._sock = None


def test_host_handler_chains_and_restores_previous_handler():
    loop = asyncio.new_event_loop()
    seen = []
    previous = lambda loop, context: seen.append(context)
    loop.set_exception_handler(previous)
    host = object.__new__(PanServer)
    host.servers = []
    try:
        with host._reset_cleanup(loop):
            context = {'message': 'unrelated'}
            loop.call_exception_handler(context)
            assert seen == [context]
        assert loop.get_exception_handler() is previous
    finally:
        loop.close()
