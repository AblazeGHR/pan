from tests.test_interaction_card_http import run_http_mcp


def test_units_real_http_stdio_production_chromium_cold_restart(tmp_path):
    run_http_mcp(tmp_path, units=True)
