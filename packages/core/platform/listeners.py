"""Address-scoped TCP listener selection; no PID guessing on query failure.

psutil supplies OS-specific enumeration on Windows/macOS/Linux. Endpoint
matching is shared, deterministic and independent of enumeration order.
"""
import ipaddress


class ListenerQueryError(RuntimeError):
    pass


def _addresses(host):
    if host == "localhost":
        return {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    try:
        address = ipaddress.ip_address(host)
    except (ValueError, TypeError) as exc:
        raise ListenerQueryError("listener binding must be a numeric address or localhost") from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return {address}


def select_owner(connections, port, host):
    targets = _addresses(host)
    owners = set()
    unknown = False
    for connection in connections:
        address = connection.laddr
        local_port = getattr(address, "port", None)
        local_host = getattr(address, "ip", None)
        if local_port is None and isinstance(address, (tuple, list)) and len(address) >= 2:
            local_host, local_port = address[:2]
        if int(local_port or -1) != int(port) or str(connection.status).upper() not in {"LISTEN", "LISTENING"}:
            continue
        addresses = _addresses(local_host)
        # IPv6 wildcard can cover IPv4 too, depending on IPV6_V6ONLY. Without
        # socket-option proof, treat cross-family wildcard as a possible
        # conflict instead of declaring the requested endpoint unoccupied.
        matches = any(a == b or a.is_unspecified and (a.version == b.version or a.version == 6)
                      or b.is_unspecified and (a.version == b.version or b.version == 6)
                      for a in addresses for b in targets)
        if not matches:
            continue
        if not connection.pid:
            unknown = True
        else:
            owners.add(int(connection.pid))
    if unknown:
        raise ListenerQueryError("matching listener PID is unavailable")
    if len(owners) > 1:
        raise ListenerQueryError("multiple processes may own the requested endpoint")
    return next(iter(owners), None)


def listener_owner(port, host="127.0.0.1"):
    import psutil
    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError) as exc:
        raise ListenerQueryError("TCP listener enumeration unavailable") from exc
    return select_owner(connections, port, host)
