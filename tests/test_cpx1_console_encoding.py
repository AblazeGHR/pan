"""Real text encoders at the isolated host console output boundary."""
import io
import sys

import pytest

from packages.core import console_exit


class Tty:
    def isatty(self):
        return True


@pytest.mark.parametrize('encoding', ['cp1252', 'ascii', 'utf-8'])
def test_console_prompt_menu_result_and_error_on_strict_stream(monkeypatch, encoding):
    raw = io.BytesIO()
    output = io.TextIOWrapper(raw, encoding=encoding, errors='strict', write_through=True)
    monkeypatch.setattr(sys, 'stdout', output)
    monkeypatch.setattr(console_exit.threading.Thread, 'start', lambda self: None)
    console = console_exit.start_console_exit(43210, lambda: 'ask', stream=Tty())
    now = [0.0]
    calls = []
    console.clock = lambda: now[0]
    console.submit = lambda payload: calls.append(payload) or {'ok': True, 'text': '完成'}
    console.line('q')
    console.tick()
    assert calls == []
    now[0] = 2.1
    console.tick()
    assert calls == [] and console.menu
    console.line('1')
    assert calls == [{'options': {'markRunningSessionsOffline': True}}] and console.accepted
    bad = console_exit.ConsoleExit(lambda _: (_ for _ in ()).throw(ValueError('失败')), lambda: 'offline')
    bad.line('qqq')
    bad.tick()
    assert not bad.accepted
    output.flush()
    text = raw.getvalue().decode(encoding)
    assert 'Pan:' in text and 'Pan Exit API:' in text and 'Pan Exit API failed' in text
    assert ('输入' in text and '失败' in text) if encoding == 'utf-8' else ('\\u8f93' in text and '\\u5931' in text)


def test_absent_stdout_uses_available_stderr(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', output)
    monkeypatch.setattr(console_exit.threading.Thread, 'start', lambda self: None)
    assert console_exit.start_console_exit(43210, lambda: 'offline', stream=Tty())
    assert '输入' in output.getvalue()


def test_broken_console_output_is_not_swallowed(monkeypatch):
    class Broken:
        encoding = 'ascii'
        def write(self, text):
            raise OSError('owned stream failed')
    monkeypatch.setattr(sys, 'stdout', Broken())
    monkeypatch.setattr(console_exit.threading.Thread, 'start', lambda self: None)
    with pytest.raises(OSError, match='owned stream failed'):
        console_exit.start_console_exit(43210, lambda: 'offline', stream=Tty())
    called = []
    console = console_exit.ConsoleExit(lambda payload: called.append(payload) or {'accepted': True},
                                       lambda: 'offline')
    console.line('qqq')
    with pytest.raises(OSError, match='owned stream failed'):
        console.tick()
    assert called == [{'options': {'preserveRunningIllegal': True}}]
    assert console.accepted  # The API accepted; the output failure is still visible.


def test_both_streams_absent_are_explicit(monkeypatch):
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    monkeypatch.setattr(console_exit.threading.Thread, 'start', lambda self: None)
    with pytest.raises(OSError, match='no console output stream'):
        console_exit.start_console_exit(43210, lambda: 'offline', stream=Tty())
