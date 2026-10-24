"""Compact navigation provenance, mirroring the browser messageFilter contract."""
import re

# ECMAScript trimStart / RegExp whitespace, not Python's lstrip.
JS_SPACE = '\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
SYSTEM = re.compile(r'^////by (?:pan system|system)(?=[' + JS_SPACE + r']|:|$)')


def is_navigation_user(row):
    """Equivalent to messageFilter.getNavigationKind(row) == 'user'.

    Report precedes System precedes structured agent provenance. Literal MA/QQ
    prefixes without provenance remain user, just as in the rail. Visibility
    toggles are client preferences; the jump validates those again at use time.
    """
    if not isinstance(row, dict) or not isinstance(row.get('content'), str):
        return False
    text = row['content'].lstrip(JS_SPACE)
    return (row.get('role') == 'user' and row.get('source') not in ('report', 'agent')
            and not text.startswith('@@@@by agent') and not SYSTEM.match(text))
