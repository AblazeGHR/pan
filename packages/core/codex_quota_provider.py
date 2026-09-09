"""Optional active Codex quota refresh through ChatGPT's WHAM endpoint."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from .codex_quota import normalize_codex_rate_limits
from .codex_quota_store import CodexQuotaStore, read_codex_credentials


WHAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
DEFAULT_TTL_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 15.0


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class QuotaRefreshResult:
    record: dict[str, Any] | None
    attempted: bool = False
    error_code: str | None = None
    credential_status: str | None = None


class CodexWhamProvider:
    """Read-only, best-effort active refresh with per-profile single-flight."""

    _locks: dict[tuple[str, int], asyncio.Lock] = {}

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        ttl_seconds: float | None = None,
        timeout_seconds: float | None = None,
        client_factory: Callable[..., Any] | None = None,
    ):
        self.enabled = _env_bool("PAN_CODEX_WHAM_ENABLED", False) if enabled is None else enabled
        self.ttl_seconds = (
            _env_float("PAN_CODEX_WHAM_TTL_SECONDS", DEFAULT_TTL_SECONDS)
            if ttl_seconds is None else max(0.1, float(ttl_seconds))
        )
        self.timeout_seconds = (
            _env_float("PAN_CODEX_WHAM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            if timeout_seconds is None else max(0.1, float(timeout_seconds))
        )
        self.client_factory = client_factory or httpx.AsyncClient

    @classmethod
    def _lock_for(cls, profile_key: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        key = (profile_key, id(loop))
        lock = cls._locks.get(key)
        if lock is None:
            lock = cls._locks[key] = asyncio.Lock()
        return lock

    async def maybe_refresh(
        self,
        store: CodexQuotaStore,
        *,
        force: bool = False,
    ) -> QuotaRefreshResult:
        current = store.load()
        if not force and current is not None and not _is_stale(current, self.ttl_seconds):
            return QuotaRefreshResult(current)
        if not self.enabled:
            return QuotaRefreshResult(current, error_code="feature_disabled")

        async with self._lock_for(store.profile.profile_key):
            current = store.load()
            if not force and current is not None and not _is_stale(current, self.ttl_seconds):
                return QuotaRefreshResult(current)

            credentials, credential_status = read_codex_credentials(store.profile)
            if credentials is None:
                return QuotaRefreshResult(
                    current,
                    attempted=False,
                    error_code="credential_unavailable",
                    credential_status=credential_status,
                )

            headers = {
                "Authorization": f"Bearer {credentials.access_token}",
                "User-Agent": "codex-cli",
                "Accept": "application/json",
            }
            account_id = credentials.chatgpt_account_id or credentials.account_id
            if account_id:
                headers["ChatGPT-Account-Id"] = account_id

            try:
                async with self.client_factory(timeout=self.timeout_seconds) as client:
                    response = await client.get(WHAM_USAGE_URL, headers=headers)
                if response.status_code in (401, 403):
                    return QuotaRefreshResult(
                        current,
                        attempted=True,
                        error_code="credential_expired",
                        credential_status="expired",
                    )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, OSError, RuntimeError):
                return QuotaRefreshResult(current, attempted=True, error_code="network_error")
            except (ValueError, TypeError):
                return QuotaRefreshResult(current, attempted=True, error_code="invalid_response")

            if not isinstance(payload, dict):
                return QuotaRefreshResult(current, attempted=True, error_code="invalid_response")
            normalized = normalize_codex_rate_limits(payload)
            if not normalized.get("valid"):
                return QuotaRefreshResult(current, attempted=True, error_code="invalid_response")

            observed = datetime.now(timezone.utc).isoformat()
            record, _ = await asyncio.to_thread(
                store.update,
                payload,
                observed_at=observed,
                received_at=observed,
                source="wham-usage",
            )
            return QuotaRefreshResult(record, attempted=True)


def _is_stale(record: dict[str, Any], ttl_seconds: float) -> bool:
    value = record.get("observedAt") or record.get("updatedAt")
    if not isinstance(value, str):
        return True
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - observed).total_seconds() > ttl_seconds
