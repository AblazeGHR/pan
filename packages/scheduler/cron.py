"""兼容垫片：cron 求值已上移 ``packages/jobs/cron.py``（P1 统一）。

保留本模块仅为既有导入路径 ``from packages.scheduler import cron``；
新代码一律直接 import ``packages.jobs.cron``。P4 收编时随插件一并退役。
"""

from packages.jobs.cron import *  # noqa: F401,F403
from packages.jobs.cron import (  # noqa: F401  私有名 import * 不携带，显式再导出
    _FIELD_SPECS,
    _KINDS,
    _day_matches,
    _from_wall,
    _naive,
    _next_cron,
    _next_interval,
    _parse_field,
    _resolve_tz,
    _to_wall,
)
