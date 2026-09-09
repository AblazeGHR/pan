"""Read-only WHAM provider tests, including degraded and single-flight paths."""

import asyncio
from pathlib import Path

import httpx

from packages.core import codex_quota_provider as provider_module
from packages.core.codex_quota_store import CodexCredentials, CodexProfile, CodexQuotaStore


def _profile():
    return CodexProfile(Path("C:/codex/provider"), "acct", "chatgpt-acct", "provider-profile")


def _payload():
    return {"rate_limit": {
        "primary_window": {"used_percent": 11, "limit_window_seconds": 18000},
        "secondary_window": {"used_percent": 44, "limit_window_seconds": 604800},
    }}


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", provider_module.WHAM_USAGE_URL)
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)


class _Client:
    def __init__(self, response=None, error=None, counter=None, delay=0):
        self.response = response
        self.error = error
        self.counter = counter
        self.delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url, headers):
        assert headers["User-Agent"] == "codex-cli"
        assert headers["Authorization"] == "Bearer secret-in-memory"
        assert headers["ChatGPT-Account-Id"] == "chatgpt-acct"
        if self.counter is not None:
            self.counter["calls"] += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.response


def _provider(monkeypatch, client_factory, *, ttl=300):
    monkeypatch.setattr(
        provider_module,
        "read_codex_credentials",
        lambda _profile: (
            CodexCredentials("secret-in-memory", "acct", "chatgpt-acct"),
            "valid",
        ),
    )
    return provider_module.CodexWhamProvider(
        enabled=True, ttl_seconds=ttl, client_factory=client_factory,
    )


def test_wham_parses_nested_windows_and_persists_source(tmp_path, monkeypatch):
    store = CodexQuotaStore(_profile(), tmp_path)
    provider = _provider(
        monkeypatch,
        lambda **_kwargs: _Client(_Response(200, _payload())),
    )
    result = asyncio.run(provider.maybe_refresh(store))

    assert result.error_code is None
    assert result.record["source"] == "wham-usage"
    assert result.record["windows"]["first"]["kind"] == "five_hour"
    assert result.record["windows"]["secondary"]["kind"] == "weekly"
    assert store.load()["rawSnapshots"]["wham-usage"] == _payload()


def test_wham_401_preserves_last_good_and_marks_credentials_expired(tmp_path, monkeypatch):
    store = CodexQuotaStore(_profile(), tmp_path)
    store.update({"secondary": {"usedPercent": 17, "windowDurationMins": 10080}})
    before = store.load()
    provider = _provider(
        monkeypatch,
        lambda **_kwargs: _Client(_Response(401)),
    )
    result = asyncio.run(provider.maybe_refresh(store, force=True))

    assert result.error_code == "credential_expired"
    assert result.credential_status == "expired"
    assert result.record == before
    assert store.load() == before


def test_wham_network_failure_preserves_last_good(tmp_path, monkeypatch):
    store = CodexQuotaStore(_profile(), tmp_path)
    store.update({"secondary": {"usedPercent": 17, "windowDurationMins": 10080}})
    before = store.load()
    request = httpx.Request("GET", provider_module.WHAM_USAGE_URL)
    provider = _provider(
        monkeypatch,
        lambda **_kwargs: _Client(error=httpx.ConnectError("offline", request=request)),
    )
    result = asyncio.run(provider.maybe_refresh(store, force=True))

    assert result.error_code == "network_error"
    assert result.record == before
    assert store.load() == before


def test_wham_single_flight_allows_one_request_per_profile(tmp_path, monkeypatch):
    store = CodexQuotaStore(_profile(), tmp_path)
    counter = {"calls": 0}
    provider = _provider(
        monkeypatch,
        lambda **_kwargs: _Client(_Response(200, _payload()), counter=counter, delay=0.02),
    )

    async def run():
        return await asyncio.gather(
            provider.maybe_refresh(store),
            provider.maybe_refresh(store),
        )

    results = asyncio.run(run())
    assert counter["calls"] == 1
    assert all(result.record["source"] == "wham-usage" for result in results)


def test_wham_feature_flag_can_disable_active_refresh(tmp_path):
    store = CodexQuotaStore(_profile(), tmp_path)
    calls = {"count": 0}

    def client_factory(**_kwargs):
        calls["count"] += 1
        return _Client(_Response(200, _payload()))

    result = asyncio.run(provider_module.CodexWhamProvider(
        enabled=False, client_factory=client_factory,
    ).maybe_refresh(store))
    assert result.error_code == "feature_disabled"
    assert calls["count"] == 0
