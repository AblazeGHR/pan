import sys, json, shutil, tempfile
sys.path.insert(0, ".")
from pathlib import Path
from packages.core.session import Session
from packages.core.adapters.kimi.adapter import KimiAdapter

s = Session(id="ses_testabc123", name="probe", adapter="kimi", model="moonshot-cn/kimi-k2.6",
            workdir=str(Path(tempfile.gettempdir())))
s.adapter_config["mcp_servers"] = [{
    "name": "pan", "type": "stdio",
    "command": "E:/software/miniforge/python.exe", "args": ["-m","pan_mcp"],
}]
a = KimiAdapter()
args = a.build_spawn_args(s)
print("ARGS:", args)
home = Path(s.adapter_config["kimi_home_dir"])
print("HOME exists:", home.exists())
print("config.toml copied:", (home/"config.toml").exists())
mcp = json.loads((home/"mcp.json").read_text(encoding="utf-8"))
print("mcp keys:", list(mcp.keys()), "servers:", list(mcp["mcpServers"].keys()))
print("pan env:", mcp["mcpServers"]["pan"].get("env"))

s2 = Session(id="ses_nomcp_xyz", name="n", adapter="kimi", model="moonshot-cn/kimi-k2.6", workdir="x")
print("NO-MCP ARGS:", a.build_spawn_args(s2))
print("NO-MCP home_dir:", s2.adapter_config.get("kimi_home_dir"))
shutil.rmtree(home, ignore_errors=True)
print("DONE")
