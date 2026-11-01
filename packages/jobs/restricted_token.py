"""Compatibility imports for Windows owned-process capabilities (until R6)."""
from packages.core.platform import supports
from packages.core.platform.process import UnsupportedPlatformError


if supports("process.hiddenDesktop"):
    from packages.core.platform.restricted_token_win import HiddenJobToken
else:
    class HiddenJobToken:
        def __init__(self, *args, **kwargs):
            raise UnsupportedPlatformError("process.hiddenDesktop unsupported on this platform")
