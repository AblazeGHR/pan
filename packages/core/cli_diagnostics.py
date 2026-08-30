"""User-facing diagnostics for the external Agent CLI adapters."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from .adapters import get_adapter, list_adapters


_LABELS = {
    "cbc": "cbc (CodeBuddy CLI)",
    "kimi": "kimi (Kimi CLI)",
    "opencode": "opencode (OpenCode CLI)",
    "claude": "claude (Claude Code CLI)",
    "codex": "codex (OpenAI Codex CLI)",
}

_HINTS = {
    "cbc": (
        "安装全局 cbc/CodeBuddy CLI，并确保 cbc（npm 全局目录）和 node "
        "都在启动 Pan 的 PATH 中；也可设置 PAN_CBC_PATH。"
    ),
    "kimi": (
        "安装 Kimi CLI。Windows 默认查找 %USERPROFILE%\\.kimi-code\\bin\\kimi.exe；"
        "其他位置请设置 PAN_KIMI_PATH。"
    ),
    "opencode": (
        "安装 OpenCode CLI，并确保 opencode（npm 全局目录）和 node 在启动 Pan 的 PATH 中；"
        "也可设置 PAN_OPENCODE_PATH。"
    ),
    "claude": (
        "安装 Claude Code CLI，并确保 claude（npm 全局目录）和 node 在启动 Pan 的 PATH 中；"
        "也可设置 PAN_CLAUDE_PATH。"
    ),
    "codex": (
        "安装 OpenAI Codex CLI，并确保 codex（npm 全局目录）和 node 在启动 Pan 的 PATH 中；"
        "也可设置 PAN_CODEX_PATH / PAN_CODEX_NODE。"
    ),
}


@dataclass(frozen=True)
class CliDiagnostic:
    name: str
    label: str
    available: bool
    command: list[str]
    missing: list[str]
    hint: str
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "available": self.available,
            "command": self.command,
            "missing": self.missing,
            "hint": self.hint,
            "error": self.error,
        }


def _is_resolvable(executable: str) -> bool:
    """Return whether *executable* is a file or can be resolved through PATH."""
    if not executable:
        return False
    try:
        if Path(executable).expanduser().is_file():
            return True
    except (OSError, ValueError):
        pass
    return shutil.which(executable) is not None


def _resolved_command(adapter) -> list[str]:
    """Ask an adapter for executable components without spawning it."""
    resolver = getattr(adapter, "resolved_cli_argv", None)
    if resolver is None:
        raise RuntimeError("adapter does not expose resolved_cli_argv()")
    return [str(part) for part in resolver()]


def check_cli_adapter(adapter_name: str) -> CliDiagnostic:
    """Resolve and check one registered adapter without invoking the CLI."""
    label = _LABELS.get(adapter_name, adapter_name)
    hint = _HINTS.get(
        adapter_name,
        "安装该 CLI，并确保它位于启动 Pan 的 PATH 中，或配置对应的路径环境变量。",
    )
    try:
        command = _resolved_command(get_adapter(adapter_name))
        missing = [part for part in command if not _is_resolvable(part)]
        return CliDiagnostic(
            name=adapter_name,
            label=label,
            available=not missing,
            command=command,
            missing=missing,
            hint=hint,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must never block startup
        return CliDiagnostic(
            name=adapter_name,
            label=label,
            available=False,
            command=[],
            missing=[],
            hint=hint,
            error=str(exc),
        )


def get_cli_diagnostics() -> list[dict]:
    """Return current availability for every registered Agent CLI adapter."""
    return [check_cli_adapter(adapter.name).as_dict() for adapter in list_adapters()]


def log_cli_preflight(logger: logging.Logger) -> list[CliDiagnostic]:
    """Log a startup summary while keeping missing optional CLIs non-fatal."""
    checks = [check_cli_adapter(adapter.name) for adapter in list_adapters()]
    available = [check for check in checks if check.available]
    logger.info(
        "[Pan] Agent CLI preflight: %d/%d supported CLI(s) available",
        len(available),
        len(checks),
    )
    for check in checks:
        if check.available:
            logger.info(
                "[Pan] CLI ready: %s -> %s",
                check.label,
                " ".join(check.command),
            )
            continue
        reason = check.error or (
            "missing: " + ", ".join(check.missing)
            if check.missing
            else "launch entry could not be resolved"
        )
        logger.warning(
            "[Pan] CLI unavailable: %s (%s). %s",
            check.label,
            reason,
            check.hint,
        )
    if not available:
        logger.error(
            "[Pan] No supported Agent CLI is available. Pan will start, but "
            "new Workers cannot run until at least one of cbc, kimi, opencode, "
            "claude, or codex is installed and visible to the Pan process."
        )
    return checks


def format_cli_spawn_error(adapter_name: str, original: BaseException | None = None) -> str:
    """Build an actionable error for a failed Worker process launch."""
    check = check_cli_adapter(adapter_name)
    prefix = f"无法启动 {check.label}。"
    if not check.available:
        detail = (
            f"当前 Pan 进程找不到启动入口（{', '.join(check.missing)}）。"
            if check.missing
            else "当前 Pan 进程无法解析它的启动入口。"
        )
        return (
            f"{prefix}{detail} {check.hint} "
            "安装或修改 PATH 后请重启 Pan；后台服务的 PATH 可能与终端不同。"
        )

    detail = f"检测到启动入口：{' '.join(check.command)}。"
    if original is not None:
        detail += f" 原始错误：{original}"
    return (
        f"{prefix}{detail} 请确认 CLI 版本可运行，并检查 Pan 进程的权限、工作目录和 PATH；"
        "修改后请重启 Pan。"
    )
