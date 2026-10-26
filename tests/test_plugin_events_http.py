from tests.support.authorization import authenticate_user_requests
"""Real owned FastAPI + Node peers + fake CLI; isolated ports and data only."""
from dataclasses import asdict
import json
from pathlib import Path

import psutil

from tests import test_real_queue_http_e2e as harness
from tests.test_automation_core import action, trigger
from tests.test_plugin_events import manifest, records, publish
