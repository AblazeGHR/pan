"""Legacy acceptance entry retired in R5-P7a.

Successor assertions: tests/test_p7a_successors.py.
Per-test reasons and coverage: evidence/r5-p7a-reds-auth-20261026/retirement-map.json.
The original protocol required the retired Engine host or legacy write routes.
"""
from __future__ import annotations

import json
from pathlib import Path

import psutil

from tests import test_real_queue_http_e2e as harness
from tests.test_automation_core import action
from tests.support.authorization import authenticate_user_requests


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts" / "system-fields-registry"
