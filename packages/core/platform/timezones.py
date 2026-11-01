"""Current host calendar zone, refreshed at every scheduling boundary.

tzlocal resolves Windows dynamic-zone names and POSIX zoneinfo configuration;
an offset sampled today is never used as a substitute for future DST rules.
"""

def host_zone_name():
    import warnings
    from packages.core.platform import supports
    if not supports("time.hostZone"):
        raise NotImplementedError("Current host timezone resolution is unsupported on this platform")
    from tzlocal import reload_localzone
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="Can not find any timezone configuration.*")
        zone = reload_localzone()
    name = getattr(zone, "key", None)
    if not name:
        raise RuntimeError("Current host timezone has no zoneinfo identity")
    return name
