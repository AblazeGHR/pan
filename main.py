#!/usr/bin/env python
"""Pan — entry point."""

import os
from datetime import datetime

from packages.web.server import app

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

    # No-auth guard (#16, resolved by policy): the API has no authentication.
    # Binding to anything but loopback exposes every endpoint on the network.
    import ipaddress
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host in ("localhost", "::1")
    if not is_loopback:
        print(
            f"[{tm}] WARNING: Pan API has NO authentication and is bound to "
            f"'{host}' — all endpoints are reachable by anyone on this network. "
            "Keep PAN_HOST at 127.0.0.1 unless you know what you are doing."
        )

    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    server.run()
