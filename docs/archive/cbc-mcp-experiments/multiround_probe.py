"""stream-json 多轮对话 + MCP 验证：同一长驻进程连续发 3 轮消息，
每轮让模型调用 MCP 工具，确认 MCP 连接与工具可用性跨轮保持。

用法:
    E:/software/miniforge/python.exe multiround_probe.py --mcp-config <path>
"""
import argparse
import asyncio
import json

CBC = ["node", "D:/node_npm/node_global/node_modules/@tencent-ai/codebuddy-code/bin/codebuddy"]

ROUNDS = [
    "请调用 MCP 工具 mcp__pan__session_list，告诉我当前有多少个会话（只报数量，一句话）。",
    "请再次调用 MCP 工具 mcp__pan__session_list，这次告诉我前 3 个会话的 name 字段（如果不足 3 个就列全部）。",
    "请第三次调用 MCP 工具 mcp__pan__model_list（adapter 参数用 cbc），告诉我返回的模型数量。如果该工具不可用，明确说明。",
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-config", default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--model", default="hy3")
    args = parser.parse_args()

    cmd = CBC + ["-p", "--output-format", "stream-json", "--input-format", "stream-json",
                 "-y", "--permission-mode", "bypassPermissions", "--model", args.model]
    if args.mcp_config:
        cmd += ["--mcp-config", args.mcp_config]
        if args.strict:
            cmd += ["--strict-mcp-config"]
    print("CMD:", " ".join(cmd), flush=True)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd="D:/project/pan-stream-mcp",
    )

    buf = b""
    round_no = 0
    cur_round_events = []  # 当前轮的 assistant 文本片段
    tool_calls = []        # (轮次, 顶层工具名)
    mcp_invocations = []   # (轮次, mcp 工具名) — 从 DeferExecuteTool rawInput.toolName 解析
    init_mcp = None

    async def send(text):
        msg = {"type": "user", "message": {"role": "user",
                                           "content": [{"type": "text", "text": text}]}}
        proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n")
        await proc.stdin.drain()

    async def drain(timeout=25.0):
        nonlocal buf
        out = b""
        try:
            chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=timeout)
            out = chunk
        except asyncio.TimeoutError:
            pass
        return out

    async def feed(limit_rounds=3):
        nonlocal round_no, buf, init_mcp
        while True:
            chunk = await drain(30.0)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("utf-8", errors="replace").strip()
                if not s:
                    continue
                try:
                    ev = json.loads(s)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type")
                if t == "system" and ev.get("subtype") == "init":
                    init_mcp = ev.get("mcp_servers")
                elif t == "assistant":
                    for b_ in ev.get("message", {}).get("content", []) or []:
                        if isinstance(b_, dict):
                            if b_.get("type") == "text":
                                cur_round_events.append(b_.get("text", ""))
                            elif b_.get("type") == "tool_use":
                                name = b_.get("name")
                                tool_calls.append((round_no, name))
                                # DeferExecuteTool 的 input 里有真正要调的 mcp 工具名
                                inp = b_.get("input") or {}
                                if name == "DeferExecuteTool" and inp.get("toolName"):
                                    mcp_invocations.append((round_no, inp["toolName"]))
                elif t == "result":
                    txt = ev.get("result", "")
                    print(f"\n===== 第 {round_no} 轮结束 =====", flush=True)
                    print(f"  文本片段: {''.join(cur_round_events)[:600]}", flush=True)
                    print(f"  本轮 tool_use: {[n for r, n in tool_calls if r == round_no]}", flush=True)
                    if round_no >= limit_rounds:
                        return
                    # 发下一轮
                    round_no += 1
                    cur_round_events.clear()
                    await send(ROUNDS[round_no - 1])

    # 发第一轮
    round_no = 1
    await send(ROUNDS[0])
    try:
        await asyncio.wait_for(feed(), timeout=240.0)
    except asyncio.TimeoutError:
        print("[超时]", flush=True)

    print(f"\n===== 汇总 =====", flush=True)
    print(f"  init mcp_servers: {json.dumps(init_mcp, ensure_ascii=False)}", flush=True)
    print(f"  各轮 tool_use: {tool_calls}", flush=True)
    print(f"  实际 MCP 工具调用 (DeferExecuteTool): {mcp_invocations}", flush=True)
    mcp_calls = [n for _, n in mcp_invocations if "mcp__" in str(n)]
    print(f"  MCP 工具调用次数: {len(mcp_calls)}", flush=True)
    proc.kill()
    await proc.wait()


if __name__ == "__main__":
    asyncio.run(main())
