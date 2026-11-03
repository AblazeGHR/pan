"""Darwin loopback listener evidence without root-only global enumeration."""
from __future__ import annotations

import socket
import sys


def needs_local_probe():
    return sys.platform == "darwin"


def loopback_listener(port):
    """Owner from per-process sockets; absence from exclusive loopback listen.

    Incomplete process visibility NEVER establishes absence. If an inaccessible
    listener occupies the address, bind/listen fails and the result is unknown.
    The scope is Pan's IPv4 loopback endpoint, not every network interface.
    """
    import psutil
    try:
        for process in psutil.process_iter():
            try:
                for connection in process.net_connections(kind="tcp4"):
                    address = connection.laddr
                    if (address and address.port == port and address.ip in {"127.0.0.1", "0.0.0.0"}
                            and str(connection.status).upper() in {"LISTEN", "LISTENING"}):
                        return {"status": "listening", "owner": process.pid, "scope": "127.0.0.1"}
            except (psutil.Error, OSError):
                continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # Reuse TIME_WAIT only; SO_REUSEPORT is deliberately never enabled.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
            probe.listen(1)
        return {"status": "offline", "owner": None, "scope": "127.0.0.1",
                "method": "exclusive_loopback_listen"}
    except (psutil.Error, OSError) as exc:
        return {"status": "unknown", "owner": None, "scope": "127.0.0.1", "reason": type(exc).__name__}
