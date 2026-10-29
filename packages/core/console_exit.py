"""Pan-host console commands. Never reads Worker or Job terminals."""
from __future__ import annotations

import json
import sys
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def _console_output(value):
    """Write to the host's existing stream, escaping unsupported glyphs only."""
    stream = sys.stdout if sys.stdout is not None else sys.stderr
    if stream is None:
        raise OSError("no console output stream")
    text = str(value)
    encoding = getattr(stream, "encoding", None)
    if encoding:
        text = text.encode(encoding, errors="backslashreplace").decode(encoding)
    print(text, file=stream)


class ConsoleExit:
    """Three q commands within two seconds override configuration to preserve Illegal.

    The first command waits for that window before submitting anything, so a
    rapid override cannot race an already accepted safe-exit request.
    """
    def __init__(self, submit, strategy, output=_console_output, *, clock=time.monotonic):
        self.submit, self.strategy, self.output, self.clock = submit, strategy, output, clock
        self.lock = threading.Lock()
        self.pending = False
        self.count = 0
        self.since = 0.0
        self.menu = False
        self.accepted = False

    def line(self, value):
        value = value.strip().lower()
        with self.lock:
            if self.accepted:
                return
            if value and set(value) == {"q"}:
                now = self.clock()
                if not self.pending or now - self.since > 2:
                    self.pending, self.count, self.since = True, 0, now
                self.count += len(value)
                return
            if self.menu and value in {"1", "2", "3"}:
                self.menu = False
                self._send({"options": {"markRunningSessionsOffline": value == "1",
                                         **({"preserveRunningIllegal": True} if value == "3" else {})}})

    def tick(self):
        with self.lock:
            if self.accepted or not self.pending:
                return
            if self.count >= 3:
                self.pending = False
                self._send({"options": {"preserveRunningIllegal": True}})
            elif self.clock() - self.since >= 2:
                self.pending = False
                if self.strategy() == "ask":
                    self.menu = True
                    self.output("1. 安全退出并将 running 标为合法 offline\n2. 退出并保留 running 为 Illegal（下次可恢复）\n3. 强制保留所有 running 为 Illegal\n输入编号后回车；连续三次 q 或 qqq 回车选择 3。")
                else:
                    self._send({})

    def _send(self, payload):
        try:
            result = self.submit(payload)
        except Exception as exc:
            self.output(f"Pan Exit API failed; service remains active: {exc}")
            return
        self.accepted = bool(result.get("accepted") or result.get("ok"))
        self.output(f"Pan Exit API: {result}")


def start_console_exit(port, strategy, *, stream=None):
    stream = sys.stdin if stream is None else stream
    if stream is None or not stream.isatty():
        return None
    def submit(payload):
        from .lifecycle_action import ACTION_ROUTE
        request = Request(f"http://127.0.0.1:{port}{ACTION_ROUTE}",
                          data=json.dumps({"operation": "exit", **payload}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            return {"ok": False, "error": exc.read().decode(errors="replace")}
    console = ConsoleExit(submit, strategy)
    _console_output("Pan: 输入 q 后回车按配置退出；2 秒内三次 q（或 qqq 回车）保留 running 为 Illegal。Ctrl+C 为非法退出。")
    stop = threading.Event()
    def read():
        try:
            for line in stream:
                console.line(line)
        finally:
            stop.set()
    def timer():
        while not stop.wait(0.05):
            console.tick()
    threading.Thread(target=read, name="pan-console-input", daemon=True).start()
    threading.Thread(target=timer, name="pan-console-exit", daemon=True).start()
    return console
