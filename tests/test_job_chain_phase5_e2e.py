"""Legacy acceptance entry retired in R5-P7a.

Successor assertions: tests/test_p7a_successors.py.
Per-test reasons and coverage: evidence/r5-p7a-reds-auth-20261026/retirement-map.json.
The original protocol required the retired Engine host or legacy write routes.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta

import psutil
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import async_test
from packages.jobs.window import command_window_options

OUT = h.ROOT / 'artifacts/job-chain-phase5'


async def _finished(runtime, jid, headers=None):
    for _ in range(400):
        job = runtime.request('GET', '/api/jobs/' + jid, **({'headers': headers} if headers else {}))['job']
        if job.get('status') in {'completed', 'failed', 'uncertain', 'unknown', 'cancelled'} and not job.get('execution'):
            return job
        await asyncio.sleep(.025)
    raise AssertionError(('Job did not settle', job))


def _ephemeral_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    return port


def _payload(result):
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)
