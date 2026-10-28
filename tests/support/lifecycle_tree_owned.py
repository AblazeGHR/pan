"""Long-lived owned child and grandchild; the retained supervisor owns cleanup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import psutil

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
rows = [{"pid": os.getpid(), "birth": psutil.Process().create_time()},
        {"pid": child.pid, "birth": psutil.Process(child.pid).create_time()}]
path = Path(sys.argv[1])
temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(rows), encoding="utf-8")
temporary.replace(path)
while True:
    time.sleep(.1)
