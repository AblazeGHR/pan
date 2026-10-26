"""Owned random-port HTTP/MCP + actual fakeCLI notification handoff."""
import asyncio
import json
import os
import subprocess
import hashlib
from pathlib import Path

import httpx
import psutil

from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action, trigger
from tests.support.authorization import authenticate_user_requests

OUT = Path('audit/locker-binding')
