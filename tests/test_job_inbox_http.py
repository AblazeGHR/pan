from tests.support.authorization import authenticate_user_requests
"""Real owned HTTP: Job receipts and Locker delivery share safe Session Queue."""
import json
from pathlib import Path
import subprocess
import os

import httpx

from tests import test_real_queue_http_e2e as h
from packages.jobs.window import command_window_options

OUT=Path('artifacts/job-inbox-followup/http')
