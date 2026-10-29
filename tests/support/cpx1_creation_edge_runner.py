"""Owned runner handshake before owner import or at suspended native creation."""
import os
from pathlib import Path
import sys
import threading

from tests.helpers import process_owner_hidden_probe as probe

root = Path(sys.argv[1])
phase = sys.argv[2]
probe.publish(root / 'runner.json', {'pid': os.getpid(), 'birth': probe.filetime(),
    'prefix': sys.prefix, 'basePrefix': sys.base_prefix})
if phase == 'pre-import':
    # No ProcessOwner has been imported or business child created yet.
    threading.Event().wait(30)
elif phase == 'post-create':
    sys.argv = [sys.argv[0], 'creation-runner', str(root)]
    probe.main()
else:
    raise ValueError('unknown owned gate')
