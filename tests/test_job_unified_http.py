from tests.support.authorization import authenticate_user_requests
"""Owned real HTTP, fake Worker/CLI and production Chromium combination."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta

import psutil

from packages.jobs.window import command_window_options
from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action

OUT=Path('artifacts/job-unified-live')
