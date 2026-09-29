"""SnowLuma account mapping: one gateway process, N isolated QQ channels."""

import pytest

from packages.qq.bot import _selected_specs
from packages.web.server import _validate_snowluma_accounts


def test_selected_snowluma_accounts_override_preserved_legacy_channels():
    cfg = {
        "plugin_id": "snowluma", "channel": "snowluma",
        "channels": [{"name": "llonebot", "ws_urls": ["ws://127.0.0.1:3002"]}],
        "snowluma": {
            "token": "shared-token",
            "accounts": [
                {"bot_uin": "111111", "ws_url": "ws://127.0.0.1:3003"},
                {"bot_uin": "222222", "ws_url": "ws://127.0.0.1:3004"},
                {"bot_uin": "333333", "ws_url": "ws://127.0.0.1:3005"},
            ],
        },
    }
    assert _selected_specs(cfg) == [
        {"name": "snowluma", "ws_urls": ["ws://127.0.0.1:3003"], "token": "shared-token", "bot_uin": "111111"},
        {"name": "snowluma2", "ws_urls": ["ws://127.0.0.1:3004"], "token": "shared-token", "bot_uin": "222222"},
        {"name": "snowluma3", "ws_urls": ["ws://127.0.0.1:3005"], "token": "shared-token", "bot_uin": "333333"},
    ]


def test_single_selected_gateway_keeps_previous_shape():
    assert _selected_specs({"channel": "snowluma", "snowluma": {"ws_urls": ["ws://127.0.0.1:3003"]}}) == [
        {"name": "snowluma", "ws_urls": ["ws://127.0.0.1:3003"], "token": None, "bot_uin": None},
    ]


@pytest.mark.parametrize("accounts", [
    [{"bot_uin": "111", "ws_url": "ws://127.0.0.1:3003"}, {"bot_uin": "111", "ws_url": "ws://127.0.0.1:3004"}],
    [{"bot_uin": "111", "ws_url": "ws://127.0.0.1:3003"}, {"bot_uin": "222", "ws_url": "ws://127.0.0.1:3003"}],
    [{"bot_uin": "111", "ws_url": "ws://0.0.0.0:3003"}],
    [{"bot_uin": "111", "ws_url": "ws://127.0.0.1:3003/?access_token=secret"}],
])
def test_accounts_reject_ambiguous_or_nonlocal_endpoints(accounts):
    with pytest.raises(ValueError):
        _validate_snowluma_accounts(accounts)
