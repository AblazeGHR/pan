"""CbcAdapter 模型解析与 TTL 缓存测试。

覆盖：
- `_parse_models_from_cbc_help` 与真实 `cbc --help` 输出格式对齐（mock 验证）
- `supported_models` 优先级：config.json 白名单 > cbc --help 解析 > 内置默认值
- TTL 缓存：有效期内不重拉、超时后自动重拉（对齐 opencode adapter 的
  _MODEL_CACHE_TTL 修复，cbc CLI 侧模型变更无需重启服务即可刷新）
- 模型额度倍率（credits）：解析口径、本地缓存读取（多账号条目按 ts 合并、
  容错）、TTL 缓存与 invalidate 一并失效
"""

import base64
import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters.cbc.adapter import (  # noqa: E402
    CbcAdapter,
    _MODEL_CACHE_TTL,
    _parse_models_from_cbc_help,
    _current_cbc_user_id,
    _fetch_credit_multipliers,
    _parse_credit_multiplier,
    _read_cloud_product_config_entries,
)
import packages.core.adapters.cbc.adapter as adapter_module  # noqa: E402


def _reset_cache():
    """重置 class 级缓存，保证每个测试从干净状态开始。"""
    CbcAdapter._cached_models = None
    CbcAdapter._cached_models_ts = 0.0
    CbcAdapter._cached_multipliers = None
    CbcAdapter._cached_multipliers_ts = 0.0


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """默认让 config 无 models 白名单；个别测试按需覆盖。

    默认把 `_parse_models_from_cbc_help` 置空，避免测试环境真的去解析 cbc shim
    / 跑 `cbc --help` 子进程。
    """
    _reset_cache()
    monkeypatch.setattr(
        CbcAdapter,
        "_cbc_config",
        property(lambda self: {"models": []}),
    )
    monkeypatch.setattr(adapter_module, "_parse_models_from_cbc_help", lambda *a, **k: [])
    yield
    _reset_cache()


# ── _parse_models_from_cbc_help（mock subprocess） ──


def test_parse_cbc_help_output(monkeypatch):
    class FakeResult:
        stdout = "Currently supported: (glm-5.2, glm-5.1, kimi-k2.7)\n"
        stderr = ""

    monkeypatch.setattr(adapter_module.subprocess, "run", lambda *a, **k: FakeResult())
    assert _parse_models_from_cbc_help(["cbc"]) == ["glm-5.2", "glm-5.1", "kimi-k2.7"]


def test_parse_cbc_help_prefers_stdout_over_stderr_only(monkeypatch):
    class FakeResult:
        stdout = ""
        stderr = "Currently supported: (deepseek-v4-pro)\n"

    monkeypatch.setattr(adapter_module.subprocess, "run", lambda *a, **k: FakeResult())
    assert _parse_models_from_cbc_help(["cbc"]) == ["deepseek-v4-pro"]


def test_parse_cbc_help_subprocess_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise OSError("cbc not found")

    monkeypatch.setattr(adapter_module.subprocess, "run", boom)
    assert _parse_models_from_cbc_help(["cbc"]) == []


def test_parse_cbc_help_no_match_returns_empty(monkeypatch):
    class FakeResult:
        stdout = "No models listed.\n"
        stderr = ""

    monkeypatch.setattr(adapter_module.subprocess, "run", lambda *a, **k: FakeResult())
    assert _parse_models_from_cbc_help(["cbc"]) == []


# ── supported_models 优先级 ──


def test_supported_models_prefers_config_whitelist(monkeypatch):
    monkeypatch.setattr(
        CbcAdapter,
        "_cbc_config",
        property(lambda self: {"models": ["custom/model-a", "custom/model-b"]}),
    )
    monkeypatch.setattr(
        adapter_module,
        "_parse_models_from_cbc_help",
        lambda *a, **k: ["should/not/be-used"],
    )
    a = CbcAdapter()
    assert a.supported_models == ["custom/model-a", "custom/model-b"]


def test_supported_models_falls_back_to_cli(monkeypatch):
    cli = ["glm-5.2", "glm-5.1"]
    monkeypatch.setattr(
        adapter_module, "_parse_models_from_cbc_help", lambda *a, **k: cli
    )
    assert CbcAdapter().supported_models == cli


def test_supported_models_falls_back_to_builtin(monkeypatch):
    a = CbcAdapter()
    assert a.supported_models == list(CbcAdapter._BUILTIN_MODELS)


# ── TTL 缓存 ──


def test_cache_within_ttl_does_not_reparse(monkeypatch):
    calls: list[int] = []

    def fake_parse(*a, **k):
        calls.append(1)
        return ["provider/model-one"]

    monkeypatch.setattr(adapter_module, "_parse_models_from_cbc_help", fake_parse)
    a = CbcAdapter()
    assert a.supported_models == ["provider/model-one"]
    assert a.supported_models == ["provider/model-one"]
    # TTL 有效期内第二次访问命中缓存，不再重新拉取
    assert len(calls) == 1


def test_cache_refreshes_after_ttl(monkeypatch):
    calls: list[int] = []

    def fake_parse(*a, **k):
        calls.append(1)
        return ["provider/model-%d" % len(calls)]

    monkeypatch.setattr(adapter_module, "_parse_models_from_cbc_help", fake_parse)
    a = CbcAdapter()
    assert a.supported_models == ["provider/model-1"]
    assert len(calls) == 1

    # 把时间戳拨回 TTL 之前，模拟缓存过期 → 下次访问应重新拉取
    CbcAdapter._cached_models_ts = time.monotonic() - _MODEL_CACHE_TTL - 1
    assert a.supported_models == ["provider/model-2"]
    assert len(calls) == 2


def test_builtin_fallback_is_cached_within_ttl(monkeypatch):
    calls: list[int] = []

    def fake_parse(*a, **k):
        calls.append(1)
        return []  # CLI 拉取失败

    monkeypatch.setattr(adapter_module, "_parse_models_from_cbc_help", fake_parse)
    a = CbcAdapter()
    assert a.supported_models == list(CbcAdapter._BUILTIN_MODELS)
    assert a.supported_models == list(CbcAdapter._BUILTIN_MODELS)
    # 回退结果同样进 TTL 缓存，不会反复调用 CLI
    assert len(calls) == 1


def test_config_whitelist_change_picked_up_after_ttl(monkeypatch):
    config: dict = {"models": ["custom/model-a"]}

    monkeypatch.setattr(
        CbcAdapter,
        "_cbc_config",
        property(lambda self: config),
    )
    a = CbcAdapter()
    assert a.supported_models == ["custom/model-a"]

    # TTL 内改配置：读到的仍是缓存旧值
    config["models"] = ["custom/model-b"]
    assert a.supported_models == ["custom/model-a"]

    # 缓存过期后重新读 config → 新白名单生效
    CbcAdapter._cached_models_ts = time.monotonic() - _MODEL_CACHE_TTL - 1
    assert a.supported_models == ["custom/model-b"]


# ── credits 倍率解析（按 CLI credits 字段真实格式锚定解析） ──


@pytest.mark.parametrize("raw,expected", [
    ("x0.08 credits", 0.08),
    ("x2.00 credits", 2.0),
    ("x1.62", 1.62),
    ("x0.00 credits", 0.0),   # 0 是有效值（免费模型），不得当作未知
    ("0.08x", 0.08),
    ("X0.08 CREDITS", 0.08),  # 大小写不敏感
    ("x0.08credits", 0.08),   # 无空格
])
def test_parse_credit_multiplier_formats(raw, expected):
    assert _parse_credit_multiplier(raw) == expected


@pytest.mark.parametrize("raw", [
    None, 123, 0.5, [], {}, "", "free", "credits", "abc",
    # 负数：无符号 search 会误显示正 0.08，必须拒绝
    "-0.08", "x-0.08 credits", "-1.62",
    # 指数与嵌数字文本：不得误读
    "1e-3", "x1e5 credits", "version 2.5 beta", "500 credits left", "x.5",
    # 数值溢出 → inf，必须拒绝而不是泄漏到展示层
    "x" + "9" * 400,
])
def test_parse_credit_multiplier_invalid(raw):
    assert _parse_credit_multiplier(raw) is None


# ── _current_cbc_user_id（JWT sub，与 CLI tryExtractUserIDFromJWTToken 同源） ──


def _fake_jwt(sub: str | None) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    claims = {} if sub is None else {"sub": sub}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.signature"


def _write_home(tmp_path: Path, monkeypatch, settings: dict | None) -> Path:
    home = tmp_path / "home"
    (home / ".codebuddy").mkdir(parents=True)
    if settings is not None:
        (home / ".codebuddy" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(adapter_module, "_codebuddy_home", lambda: home / ".codebuddy")
    return home / ".codebuddy"


def test_current_cbc_user_id_reads_jwt_sub(tmp_path, monkeypatch):
    _write_home(tmp_path, monkeypatch, {"env": {"CODEBUDDY_AUTH_TOKEN": _fake_jwt("user-a")}})
    assert _current_cbc_user_id() == "user-a"


@pytest.mark.parametrize("settings", [
    None,                                                    # settings.json 缺失
    {},                                                      # 无 env
    {"env": {}},                                             # 无 token
    {"env": {"CODEBUDDY_AUTH_TOKEN": "not-a-jwt"}},
    {"env": {"CODEBUDDY_AUTH_TOKEN": _fake_jwt(None)}},      # 无 sub claim
    {"env": {"CODEBUDDY_AUTH_TOKEN": _fake_jwt("  ")}},      # 空白 sub
])
def test_current_cbc_user_id_unavailable(tmp_path, monkeypatch, settings):
    _write_home(tmp_path, monkeypatch, settings)
    assert _current_cbc_user_id() is None


# ── _fetch_credit_multipliers（CLI 同规则：当前账号 + userId 精确匹配） ──


def _cloud_entry(user_id: str, models: list, ts: object = 1000) -> dict:
    return {"userId": user_id, "ts": ts, "data": {"models": models}}


def _patch_entries(monkeypatch, entries: list) -> None:
    monkeypatch.setattr(adapter_module, "_current_cbc_user_id", lambda: "user-a")
    monkeypatch.setattr(adapter_module, "_read_cloud_product_config_entries", lambda: entries)


def test_fetch_credit_multipliers_uses_current_user_entry_only(monkeypatch):
    _patch_entries(monkeypatch, [
        _cloud_entry("user-b", [{"id": "m1", "credits": "x0.10 credits"}]),
        _cloud_entry("user-a", [{"id": "m1", "credits": "x0.08 credits"}]),
    ])
    # 只读当前账号条目：不跨账号合并、不按 ts 挑选
    assert _fetch_credit_multipliers() == {"m1": 0.08}


def test_fetch_credit_multipliers_other_account_only_returns_empty(monkeypatch):
    _patch_entries(monkeypatch, [
        _cloud_entry("user-b", [{"id": "m1", "credits": "x0.08 credits"}]),
    ])
    assert _fetch_credit_multipliers() == {}


def test_fetch_credit_multipliers_without_current_user_returns_empty(monkeypatch):
    monkeypatch.setattr(adapter_module, "_current_cbc_user_id", lambda: None)
    monkeypatch.setattr(adapter_module, "_read_cloud_product_config_entries",
                        lambda: [_cloud_entry("user-a", [{"id": "m1", "credits": "x0.08 credits"}])])
    # 无法确定当前账号：宁可不显示，也不用其他数据猜测
    assert _fetch_credit_multipliers() == {}


def test_fetch_credit_multipliers_same_user_duplicates_conflict_omitted(monkeypatch):
    # CLI writer 不会产生同账号多条（先 splice 再 push）；仅防御文件损坏：
    # 冲突值不按 ts 覆盖，只保留无歧义的模型。
    _patch_entries(monkeypatch, [
        _cloud_entry("user-a", [
            {"id": "m1", "credits": "x0.08 credits"},
            {"id": "m2", "credits": "x0.10 credits"},
        ], ts=1000),
        _cloud_entry("user-a", [
            {"id": "m1", "credits": "x0.08 credits"},   # 一致 → 保留
            {"id": "m2", "credits": "x0.30 credits"},   # 冲突 → 不显示
        ], ts=2000),
    ])
    assert _fetch_credit_multipliers() == {"m1": 0.08}


def test_fetch_credit_multipliers_garbage_ts_ignored(monkeypatch):
    _patch_entries(monkeypatch, [
        _cloud_entry("user-a", [{"id": "m1", "credits": "x0.08 credits"}], ts="not-a-number"),
        _cloud_entry("user-a", [{"id": "m1", "credits": "x0.08 credits"}], ts=None),
    ])
    # ts 不参与选择/覆盖；坏 ts 不影响读取
    assert _fetch_credit_multipliers() == {"m1": 0.08}


def test_fetch_credit_multipliers_skips_malformed_entries(monkeypatch):
    _patch_entries(monkeypatch, [
        {"userId": "user-a", "ts": 1, "data": []},
        {"userId": "user-a", "ts": 2, "data": {"models": "nope"}},
        {"userId": "user-a", "ts": 3, "data": {"models": ["not-a-dict"]}},
        _cloud_entry("user-a", [{"id": "m1", "credits": "x0.08 credits"}]),
    ])
    assert _fetch_credit_multipliers() == {"m1": 0.08}


def test_fetch_credit_multipliers_skips_invalid_credit_values(monkeypatch):
    _patch_entries(monkeypatch, [
        _cloud_entry("user-a", [
            {"id": "no-credits"},                          # 缺字段 → 无倍率
            {"id": "none-credits", "credits": None},
            {"id": "num-credits", "credits": 0.08},        # 非字符串 → 无倍率
            {"id": "text-credits", "credits": "free"},
            {"id": "neg-credits", "credits": "x-0.08 credits"},  # 负数 → 无倍率
            {"id": "", "credits": "x0.08 credits"},        # 空 id → 丢弃
            {"id": "ok", "credits": "x0.08 credits"},
            {"id": "zero", "credits": "x0.00 credits"},
        ]),
    ])
    assert _fetch_credit_multipliers() == {"ok": 0.08, "zero": 0.0}


# ── _read_cloud_product_config_entries（CLI 键名单文件有界读取） ──


def _write_cloud_cache(home: Path, text: str) -> None:
    name = "entry_" + hashlib.md5(b"cloud_product_config_cache").hexdigest() + ".info"
    (home / "local_storage").mkdir(parents=True, exist_ok=True)
    (home / "local_storage" / name).write_text(text, encoding="utf-8")


def test_read_cloud_product_config_entries_reads_cli_keyed_file(tmp_path, monkeypatch):
    home = _write_home(tmp_path, monkeypatch, {})
    _write_cloud_cache(home, json.dumps([_cloud_entry("user-a", [])]))
    assert _read_cloud_product_config_entries() == [_cloud_entry("user-a", [])]


def test_read_cloud_product_config_entries_missing_file(tmp_path, monkeypatch):
    _write_home(tmp_path, monkeypatch, {})
    assert _read_cloud_product_config_entries() == []


def test_read_cloud_product_config_entries_corrupt_file(tmp_path, monkeypatch):
    home = _write_home(tmp_path, monkeypatch, {})
    _write_cloud_cache(home, "not json at all")
    assert _read_cloud_product_config_entries() == []


def test_read_cloud_product_config_entries_oversized_file(tmp_path, monkeypatch):
    home = _write_home(tmp_path, monkeypatch, {})
    monkeypatch.setattr(adapter_module, "_LOCAL_STORAGE_MAX_FILE_BYTES", 64)
    _write_cloud_cache(home, json.dumps([
        _cloud_entry("user-a", [{"id": "m1", "credits": "x0.08 credits", "pad": "x" * 200}]),
    ]))
    assert _read_cloud_product_config_entries() == []


def test_read_cloud_product_config_entries_non_list(tmp_path, monkeypatch):
    home = _write_home(tmp_path, monkeypatch, {})
    _write_cloud_cache(home, json.dumps({"userId": "user-a"}))
    assert _read_cloud_product_config_entries() == []


# ── model_credit_multipliers TTL 缓存 ──


def test_multipliers_cache_within_ttl_does_not_refetch(monkeypatch):
    calls: list[int] = []

    def fake_fetch():
        calls.append(1)
        return {"glm-5.3-flash": 0.08}

    monkeypatch.setattr(adapter_module, "_fetch_credit_multipliers", fake_fetch)
    a = CbcAdapter()
    assert a.model_credit_multipliers == {"glm-5.3-flash": 0.08}
    assert a.model_credit_multipliers == {"glm-5.3-flash": 0.08}
    assert len(calls) == 1


def test_multipliers_cache_refreshes_after_ttl(monkeypatch):
    calls: list[int] = []

    def fake_fetch():
        calls.append(1)
        return {"glm-5.3-flash": 0.08 * len(calls)}

    monkeypatch.setattr(adapter_module, "_fetch_credit_multipliers", fake_fetch)
    a = CbcAdapter()
    assert a.model_credit_multipliers == {"glm-5.3-flash": 0.08}
    assert len(calls) == 1

    CbcAdapter._cached_multipliers_ts = time.monotonic() - _MODEL_CACHE_TTL - 1
    assert a.model_credit_multipliers == {"glm-5.3-flash": 0.16}
    assert len(calls) == 2


def test_invalidate_models_cache_clears_multipliers(monkeypatch):
    monkeypatch.setattr(adapter_module, "_fetch_credit_multipliers",
                        lambda: {"glm-5.3-flash": 0.08})
    a = CbcAdapter()
    assert a.model_credit_multipliers == {"glm-5.3-flash": 0.08}

    CbcAdapter.invalidate_models_cache()
    assert CbcAdapter._cached_multipliers is None
    assert CbcAdapter._cached_multipliers_ts == 0.0

    # 失效后立即重新拉取（config reload 热路径）
    monkeypatch.setattr(adapter_module, "_fetch_credit_multipliers",
                        lambda: {"glm-5.3-flash": 0.16})
    assert a.model_credit_multipliers == {"glm-5.3-flash": 0.16}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
