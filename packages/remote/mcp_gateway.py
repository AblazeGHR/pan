"""Independent Pan MCP gateway protected by Cloudflare Access JWTs."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import jwt
from starlette.responses import JSONResponse


class AccessGuard:
    def __init__(self, app, issuer, audience, key_client=None):
        self.app = app
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.keys = key_client or jwt.PyJWKClient(self.issuer + "/cdn-cgi/access/certs", timeout=5)

    def verify(self, token):
        key = self.keys.get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], issuer=self.issuer,
                          audience=self.audience, options={"require": ["exp", "iat", "iss", "aud", "sub"]})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        token = headers.get(b"cf-access-jwt-assertion", b"").decode("ascii", errors="ignore")
        if not token:
            return await JSONResponse({"error": "Cloudflare Access authentication required"}, status_code=401)(scope, receive, send)
        try:
            await asyncio.to_thread(self.verify, token)
        except (jwt.PyJWTError, ValueError, OSError):
            return await JSONResponse({"error": "Invalid Access identity"}, status_code=401)(scope, receive, send)
        return await self.app(scope, receive, send)


def validate_config(config):
    issuer = config["access_issuer"].rstrip("/")
    host = urlsplit(issuer).hostname or ""
    if not issuer.startswith("https://") or not host.endswith(".cloudflareaccess.com") or "REPLACE" in issuer:
        raise ValueError("Set the actual Cloudflare Access team issuer first")
    audience = config["access_audience"]
    if not audience or "REPLACE" in audience:
        raise ValueError("Set the actual Access application AUD first")
    public_host = config["public_hostname"]
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", public_host):
        raise ValueError("Set a hostname without scheme, path or port")
    return issuer, audience, public_host

def create_app(config):
    issuer, audience, public_host = validate_config(config)
    root = Path(config["pan_project_root"]).resolve()
    os.environ["PAN_API_URL"] = config["pan_api_url"]
    os.environ.pop("PAN_AGENT_SESSION_ID", None)
    os.environ.pop("PAN_AGENT_SESSION_TITLE", None)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from packages.mcp.server import mcp
    from mcp.server.transport_security import TransportSecuritySettings
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.settings.transport_security = TransportSecuritySettings(
        allowed_hosts=[public_host, public_host + ":443", "127.0.0.1:*", "localhost:*"],
        allowed_origins=["https://" + public_host])
    return AccessGuard(mcp.streamable_http_app(), issuer, audience)


if __name__ == "__main__":
    import uvicorn
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("gateway.config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    uvicorn.run(create_app(config), host="127.0.0.1", port=config.get("port", 9742), access_log=False)

