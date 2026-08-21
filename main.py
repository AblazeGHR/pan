#!/usr/bin/env python
"""Pan — entry point."""

import logging
import os

from packages.web.server import app

_log = logging.getLogger("pan")

if __name__ == "__main__":
    import uvicorn
    from packages.core.config import load_config
    from packages.core.logging_setup import setup_logging

    # 本地日志：data/logs/pan.log（大小/天轮转）+ console 双输出
    setup_logging()

    host = os.environ.get("PAN_HOST", "127.0.0.1")
    env_port = os.environ.get("PAN_PORT")
    if env_port is not None:
        port = int(env_port)
    else:
        port = load_config().get("port", 8768)

    _log.info("Pan starting on %s:%s", host, port)

    # No-auth guard (#16, resolved by policy): the API has no authentication.
    # Binding to anything but loopback exposes every endpoint on the network.
    import ipaddress
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host in ("localhost", "::1")
    if not is_loopback:
        _log.warning(
            "Pan API has NO authentication and is bound to '%s' — all endpoints "
            "are reachable by anyone on this network. Keep PAN_HOST at 127.0.0.1 "
            "unless you know what you are doing.",
            host,
        )

    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    server.run()
