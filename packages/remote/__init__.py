"""Pan Remote Channel — remote access via Cloudflare Tunnel.

The Remote channel is an independent process that exposes the local Pan Core
(web server) to the public internet through cloudflared. It does not import any
internal modules from packages.core; it only reads config.json for the local
port and uses cloudflared's CLI to create the tunnel.

Usage:
    python -m packages.remote          # start remote channel

Configuration (config.json):
    remote.enabled      bool  # whether to auto-start tunnel
    remote.provider     str   # only "cloudflare" supported
    remote.quick_tunnel bool  # true = temporary trycloudflare.com URL
    remote.config_path  str   # path to cloudflared config.yml (named tunnel)
    remote.binary_path  str   # path to cloudflared binary, or "cloudflared"
    remote.status_port  int   # HTTP status server port
"""
