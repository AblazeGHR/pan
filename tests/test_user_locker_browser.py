"""Real isolated full server + production build + Chromium user panel."""
from test_interaction_card_http import run_http_mcp

def test_locker_browser_http_mcp(tmp_path):
    run_http_mcp(tmp_path,browser=True)
