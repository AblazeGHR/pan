"""ACP full probe: initialize + session/new + session/prompt，检查 MCP 工具是否加载。

三种方式分别测：
  --mcp-config <file>   : cbc CLI 参数加载 MCP
  --session-mcp         : 在 session/new 的 params 里传 mcpServers
  --remote-sse          : session/new 的 mcpServers 用 sse 远程（需先起常驻 server）
"""
import argparse
import asyncio
import json

CBC = ["node", "D:/node_npm/node_global/node_modules/@tencent-ai/codebuddy-code/bin/codebuddy"]
PAN_MCP_STDIO = [{
    "type": "stdio",
    "command": "D:/project/Pan/.venv/Scripts/python.exe",
    "args": ["-m", "packages.mcp.server"],
    "cwd": "D:/project/Pan",
}]
PAN_MCP_SSE = [{
    "id": "pan",
    "name": "pan",
    "type": "sse",
    "url": "http://127.0.0.1:9740/sse",
    "headers": [],
    "env": [],
}]


class Acp:
    def __init__(self, proc, stdin, stdout):
        self.proc = proc
        self.stdin = stdin
        self.stdout = stdout
        self.events = []

    async def _read(self, timeout=45.0):
        try:
            line = await asyncio.wait_for(self.stdout.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        if not line:
            return None
        line = line.decode("utf-8", errors="replace").strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            print("  [raw]", line[:300])
            return None

    async def send(self, msg):
        self.stdin.write(json.dumps(msg).encode("utf-8") + b"\n")
        await self.stdin.drain()

    async def collect_until(self, pred, timeout=60.0, cap=200):
        """读事件直到 pred(msg) 为真，或超时/达到上限。返回命中消息。"""
        for _ in range(cap):
            msg = await self._read(timeout=timeout)
            if msg is None:
                return None
            self.events.append(msg)
            if pred(msg):
                return msg
        return None

    def dump_tool_names(self, tag=""):
        tools = []

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "name" and isinstance(v, str):
                        tools.append(v)
                    else:
                        walk(v)
            elif isinstance(o, list):
                for i in o:
                    walk(i)

        for ev in self.events:
            walk(ev)
        uniq = sorted(set(tools))
        mcp = [t for t in uniq if "mcp" in t.lower() or "pan" in t.lower()]
        print(f"  [{tag}] 工具总数={len(uniq)}  MCP/pan 相关={mcp if mcp else '(无)'}")
        if len(uniq) <= 150:
            print(f"  [{tag}] 全部工具名: {uniq}")
        return mcp


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-config", default=None)
    parser.add_argument("--session-mcp", action="store_true")
    parser.add_argument("--remote-sse", action="store_true")
    parser.add_argument("--prompt", action="store_true", help="发一条 prompt 验证工具真实可用")
    parser.add_argument("--model", default="hy3")
    args = parser.parse_args()

    cmd = CBC + ["--acp", "--model", args.model, "-y", "--permission-mode", "bypassPermissions"]
    if args.mcp_config:
        cmd += ["--mcp-config", args.mcp_config, "--strict-mcp-config"]
    print("CMD:", " ".join(cmd), flush=True)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd="D:/project/pan-stream-mcp",
    )
    a = Acp(proc, proc.stdin, proc.stdout)
    try:
        # 1. initialize
        await a.send({"jsonrpc": "2.0", "method": "initialize", "id": 1,
                      "params": {"protocolVersion": 1, "clientCapabilities": {},
                                 "clientInfo": {"name": "pan-probe", "version": "0.0.1"}}})
        r = await a.collect_until(lambda m: m.get("id") == 1)
        print("== initialize OK ==", flush=True)

        # 2. session/new
        params = {"cwd": "D:/project/pan-stream-mcp"}
        if args.session_mcp:
            params["mcpServers"] = PAN_MCP_SSE if args.remote_sse else PAN_MCP_STDIO
        else:
            params["mcpServers"] = []
        await a.send({"jsonrpc": "2.0", "method": "session/new", "id": 2, "params": params})
        r = await a.collect_until(lambda m: m.get("id") == 2)
        if r is None:
            print("!! session/new 无响应", flush=True)
        else:
            print("== session/new result keys:", list(r.get("result", {}).keys()), flush=True)
            res = r.get("result", {})
            # 只打印与 tool 相关的字段
            for k in ("sessionId", "tools", "availableTools", "mcpServers", "sessionUpdate"):
                if k in res:
                    print(f"  {k} = {json.dumps(res[k], ensure_ascii=False)[:1500]}", flush=True)
            if "error" in r:
                print("  ERROR:", json.dumps(r["error"], ensure_ascii=False)[:1500], flush=True)
        a.dump_tool_names("after session/new")

        # 3. 读一会事件，找工具列表/tool 更新
        if args.mcp_config or args.session_mcp:
            for _ in range(30):
                m = await a._read(timeout=15.0)
                if m is None:
                    break
                a.events.append(m)
            a.dump_tool_names("after events")
            # 统计事件类型分布
            from collections import Counter
            types = Counter()
            for ev in a.events:
                upd = ev.get("params", {}).get("update", {})
                types[upd.get("sessionUpdate") or ev.get("method") or "?"] += 1
            print("  事件类型分布:", dict(types), flush=True)
            # dump 含 tool 关键字的完整事件
            for ev in a.events:
                s = json.dumps(ev, ensure_ascii=False)
                if "tool" in s.lower() or "mcp" in s.lower():
                    print("  [tool 事件]", s[:800], flush=True)

        # 4. session/prompt 验证
        if args.prompt:
            print("\n== session/prompt ==", flush=True)
            # 从 session/new 的 result 提取 sessionId
            sid = None
            for ev in a.events:
                if ev.get("id") == 2 and "result" in ev:
                    sid = ev["result"].get("sessionId")
                    break
            if not sid:
                print("!! 无 sessionId，跳过 prompt", flush=True)
                return
            await a.send({"jsonrpc": "2.0", "method": "session/prompt", "id": 3,
                          "params": {"sessionId": sid, "prompt": [{"type": "text", "text": "直接调用工具 mcp__pan__session_list 获取当前会话列表，然后原样告诉我返回结果。如果该工具不存在或调用失败，请明确说明错误信息。不要编造。"}]}})
            for _ in range(200):
                m = await a._read(timeout=60.0)
                if m is None:
                    break
                a.events.append(m)
                t = json.dumps(m, ensure_ascii=False)
                if '"sessionUpdate":"session_end"' in t or '"session_end"' in t:
                    print("  [session_end]", flush=True)
                    break
                if m.get("id") == 3:
                    print("  [prompt result]", json.dumps(m.get("result"), ensure_ascii=False)[:800], flush=True)
            # 打印模型回复文本
            for ev in a.events:
                upd = ev.get("params", {}).get("update", {})
                if upd.get("sessionUpdate") == "agent_message_chunk":
                    txt = upd.get("content", {}).get("text", "")
                    print("  [chunk]", txt, end="", flush=True)
                if upd.get("sessionUpdate") == "agent_message":
                    txt = upd.get("content", {}).get("text", "")
                    print("\n  [agent_message]", json.dumps(txt, ensure_ascii=False)[:2000], flush=True)
                if upd.get("sessionUpdate") == "tool_call":
                    print("\n  [tool_call]", json.dumps(upd, ensure_ascii=False)[:600], flush=True)
            print("", flush=True)
            a.dump_tool_names("after prompt")
    finally:
        proc.kill()
        await proc.wait()


if __name__ == "__main__":
    asyncio.run(main())
