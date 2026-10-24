"""Pan 本地化日志配置。

由 main.py 启动时调用一次 ``setup_logging()``：根 logger 同时挂
文件（大小/天双轮转）+ console 两个 handler。所有模块（worker、server 等）
通过标准 logging 打日志，文件与 stdout/stderr 双写。

config.json 可配置（packages/core/config.py 的 DEFAULT_CONFIG["logging"]）：

    "logging": {
        "level": "INFO",             # DEBUG/INFO/WARNING/ERROR
        "file": "data/logs/pan.log", # 相对路径以仓库根为基准
        "max_bytes": 10485760,       # 单文件大小上限，超过即轮转
        "backup_count": 7,           # 保留的轮转日志份数
        "console": true,             # 是否同时输出到控制台
        # 整文件轮转（进程外写入、不能 truncate 的日志）
        "console_max_bytes": 20971520,          # pan-console.out/err.log
        "console_backup_count": 3,
        "session_error_max_bytes": 20971520,    # session-errors.jsonl
        "session_error_backup_count": 3
    }

``rotate_whole_file()`` 是 Pan 唯一的“整文件 rename 再开新文件”原语：
console 转发器（``console_logs``）与 session error 采样（``session_error_log``）
共用它，避免再造第三套平行轮转。
"""

from __future__ import annotations

import glob
import logging
import logging.handlers
import os
import re
import stat
from datetime import datetime
from pathlib import Path

from .config import load_config

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 仓库根：packages/core/logging_setup.py -> packages/core -> packages -> root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_configured = False

_DAILY_SUFFIX_RE = re.compile(r"\.\d{8}$")  # pan.log.20260816

# 整文件轮转默认值：active + 3 份备份，单文件 20 MiB。
DEFAULT_BOUNDED_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_BOUNDED_BACKUP_COUNT = 3
MIN_BOUNDED_MAX_BYTES = 1024
MAX_BOUNDED_MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_BOUNDED_BACKUP_COUNT = 20

# Windows 上 reparse point（junction/symlink）属性位；不走别名轮转。
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _today_stamp() -> str:
    return datetime.now().strftime("%Y%m%d")


class _SizeTimeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """按大小 + 按天轮转的文件 handler。

    - 文件超过 ``max_bytes`` -> 按 RotatingFileHandler 的编号后缀轮转
      （pan.log -> pan.log.1 -> pan.log.2 ...）
    - 跨天后的首条日志 -> 将昨日内容重命名为 ``pan.log.YYYYMMDD`` 后开新文件
    两种轮转各自保留最近 ``backup_count`` 份。
    """

    def __init__(self, filename: str, max_bytes: int, backup_count: int,
                 encoding: str = "utf-8"):
        super().__init__(filename, maxBytes=max_bytes,
                         backupCount=backup_count, encoding=encoding)
        self._day = _today_stamp()

    def shouldRollover(self, record) -> bool:
        if super().shouldRollover(record):
            return True
        return _today_stamp() != self._day

    def doRollover(self) -> None:
        day_rollover = _today_stamp() != self._day
        stamp = self._day  # 旧文件覆盖的那一天，用于命名按天轮转文件
        self._day = _today_stamp()
        if day_rollover:
            # 跨天：整文件改名带日期戳（旧文件所覆盖的那一天），重开新文件
            if self.stream:
                self.stream.close()
                self.stream = None
            dfn = f"{self.baseFilename}.{stamp}"
            if os.path.exists(dfn):
                os.remove(dfn)
            if os.path.exists(self.baseFilename):
                os.rename(self.baseFilename, dfn)
        else:
            super().doRollover()
        if not self.stream:
            self.stream = self._open()
        self._prune_daily()

    def _prune_daily(self) -> None:
        """按修改时间保留最近 backup_count 个日期后缀的旧文件。"""
        if self.backupCount <= 0:
            return
        base = os.path.basename(self.baseFilename)
        candidates = [
            p for p in glob.glob(f"{self.baseFilename}.*")
            if os.path.basename(p) != base and _DAILY_SUFFIX_RE.search(p)
        ]
        candidates.sort(key=os.path.getmtime, reverse=True)
        for old in candidates[self.backupCount:]:
            try:
                os.remove(old)
            except OSError:
                pass


def _resolve_log_path(cfg: dict) -> Path:
    raw = str(cfg.get("file", "data/logs/pan.log"))
    p = Path(raw)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    return p


def setup_logging() -> logging.Logger:
    """配置 Pan 根日志：文件（大小/天轮转）+ console 双输出。幂等。"""
    global _configured
    if _configured:
        return logging.getLogger()

    cfg = load_config().get("logging", {})
    level = getattr(logging, str(cfg.get("level", "INFO")).upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # 文件 handler（大小/天双轮转）
    log_path = _resolve_log_path(cfg)
    # The session error sample log lives beside the configured Pan log.  Its
    # bounds are the operator-configured whole-file rotation limits.
    from . import session_error_log
    session_max, session_backups = bounded_rotation_settings(
        cfg, max_key="session_error_max_bytes", backup_key="session_error_backup_count")
    session_error_log.configure(
        log_path.parent / "session-errors.jsonl",
        max_bytes=session_max,
        backup_count=session_backups,
    )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = _SizeTimeRotatingFileHandler(
            str(log_path),
            max_bytes=int(cfg.get("max_bytes", 10 * 1024 * 1024)),
            backup_count=int(cfg.get("backup_count", 7)),
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        logging.getLogger(__name__).warning(
            "Cannot open log file %s: %s", log_path, e
        )

    # console handler
    if cfg.get("console", True):
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)

    _configured = True
    return root


def _bounded_positive_int(value: object, default: int, minimum: int, maximum: int) -> int:
    """A configured positive int, or the default when absent/invalid.

    ``bool`` is rejected on purpose: ``true`` must not become ``1`` byte.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if not minimum <= value <= maximum:
        return default
    return value


def bounded_rotation_settings(
    cfg: dict | None, *, max_key: str, backup_key: str,
    default_max: int = DEFAULT_BOUNDED_MAX_BYTES,
    default_backups: int = DEFAULT_BOUNDED_BACKUP_COUNT,
) -> tuple[int, int]:
    """Resolve one whole-file rotation policy with safe defaults.

    Malformed values never disable logging and never produce a truncating
    configuration: they fall back to the documented default.
    """
    section = cfg if isinstance(cfg, dict) else {}
    return (
        _bounded_positive_int(
            section.get(max_key), default_max, MIN_BOUNDED_MAX_BYTES, MAX_BOUNDED_MAX_BYTES),
        _bounded_positive_int(
            section.get(backup_key), default_backups, 1, MAX_BOUNDED_BACKUP_COUNT),
    )


def _is_plain_file_entry(path: Path) -> bool:
    """True only for a real file entry Pan may rename.

    Refuses directories, symlinks/junctions (Windows reparse points) and hard
    links, so rotation never follows an alias or rotates a second name for the
    same bytes.  The path itself is always built by Pan inside its log
    directory; this check is about the entry, not about resolving parents.
    """
    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    if getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE:
        return False
    return getattr(info, "st_nlink", 1) == 1


def rotate_whole_file(path: str | os.PathLike[str], backup_count: int) -> bool:
    """Rename ``path`` to ``path.1`` and shift numbered backups; never truncate.

    The caller must own serialization (a cross-process lock or a single writer
    thread) and must hold no open handle to the active file: Windows refuses to
    rename a file that is still open without ``FILE_SHARE_DELETE``.

    Returns ``False`` instead of raising when the active file is missing, is a
    link/junction, or could not be moved (for example another process still
    holds it).  A ``False`` result leaves the active file untouched so the
    writer can keep appending and report a degraded (unbounded-for-now) state.

    Backups are shifted *before* the active file is moved.  If the active move
    then fails, one already-shifted older backup may have been overwritten;
    live lines are never affected.
    """
    target = Path(path)
    if not _is_plain_file_entry(target):
        return False
    backups = max(1, int(backup_count))
    try:
        for index in range(backups - 1, 0, -1):
            older = target.with_name(f"{target.name}.{index}")
            newer = target.with_name(f"{target.name}.{index + 1}")
            if not older.exists():
                continue
            if not _is_plain_file_entry(older):
                return False
            if newer.exists() and not _is_plain_file_entry(newer):
                return False
            os.replace(older, newer)
        first = target.with_name(f"{target.name}.1")
        if first.exists() and not _is_plain_file_entry(first):
            return False
        os.replace(target, first)
    except OSError:
        return False
    return True
