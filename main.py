#!/usr/bin/env python
"""Pan — entry point."""

import os
from datetime import datetime

from packages.core.server import app

if __name__ == "__main__":
    import uvicorn
    from packages.core.config import load_config

    host = os.environ.get("PAN_HOST", "127.0.0.1")
    env_port = os.environ.get("PAN_PORT")
    if env_port is not None:
        port = int(env_port)
    else:
        port = load_config().get("port", 8767)

    tm = datetime.now().strftime("%H:%M:%S")
    print(f"[{tm}] Pan starting on {host}:{port}")

    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    server.run()
