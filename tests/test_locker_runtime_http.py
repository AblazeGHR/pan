"""Legacy acceptance entry retired in R5-P7a.

Successor assertions: tests/test_p7a_successors.py.
Per-test reasons and coverage: evidence/r5-p7a-reds-auth-20261026/retirement-map.json.
The original protocol required the retired Engine host or legacy write routes.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import httpx
import psutil

from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action, trigger
from tests.support.authorization import authenticate_user_requests

OUT=Path('audit/locker-runtime/http')
