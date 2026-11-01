"""Legacy acceptance entry retired in R5-P7a.

Successor assertions: tests/test_p7a_successors.py.
Per-test reasons and coverage: evidence/r5-p7a-reds-auth-20261026/retirement-map.json.
The original protocol required the retired Engine host or legacy write routes.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import psutil
import httpx

from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action
from packages.jobs.window import command_window_options
