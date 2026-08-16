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
        "console": true              # 是否同时输出到控制台
    }
"""

from __future__ import annotations

import glob
import logging
import logging.handlers
import os
import re
from datetime import datetime
from pathlib import Path

from .config import load_config

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 仓库根：packages/core/logging_setup.py -> packages/core -> packages -> root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_configured = False

_DAILY_SUFFIX_RE = re.compile(r"\.\d{8}$")  # pan.log.20260816


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
