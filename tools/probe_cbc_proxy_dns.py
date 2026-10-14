"""Compare CBC endpoint DNS using owned Mihomo profiles; no provider turns.

Only anonymous HEAD / and DNS are used. Never mutates the user's proxy or CBC.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import struct
import tempfile
import threading
import time

import httpx
import yaml


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        assert port not in (8767, 8768)
        return port


class DNSFixture:
    """An owned resolver deliberately returning an unusable TEST-NET address."""
    def __enter__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        assert self.port not in (8767, 8768)
        self.socket.settimeout(.2)
        self.stop = threading.Event()
        def serve():
            while not self.stop.is_set():
                try:
                    data, address = self.socket.recvfrom(4096)
                    index = 12
                    while data[index]:
                        index += data[index] + 1
                    index += 1
                    qtype = struct.unpack("!H", data[index:index + 2])[0]
                    question = data[12:index + 4]
                    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton("203.0.113.1") if qtype == 1 else b""
                    response = data[:2] + struct.pack("!HHHHH", 0x8180, 1, bool(answer), 0, 0) + question + answer
                    self.socket.sendto(response, address)
                except socket.timeout:
                    continue
                except OSError:
                    if not self.stop.is_set():
                        raise
        self.thread = threading.Thread(target=serve)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.socket.close()
        self.thread.join(2)
        assert not self.thread.is_alive()


def probe(executable, policy, fixture_port=None):
    with tempfile.TemporaryDirectory(prefix="pan-cbc-dns-") as directory:
        root = Path(directory)
        proxy_port, controller_port = free_port(), free_port()
        while controller_port == proxy_port:
            controller_port = free_port()
        dns = {"enable": True, "ipv6": False, "enhanced-mode": "fake-ip",
               "fake-ip-filter": ["*"], "default-nameserver": ["tls://223.5.5.5"],
               "nameserver": ["https://doh.pub/dns-query", "https://dns.alidns.com/dns-query"]}
        if fixture_port:
            dns["nameserver"] = [f"127.0.0.1:{fixture_port}"]
        if policy == "domain_system":
            dns["nameserver-policy"] = {"copilot.tencent.com": ["system"]}
        elif policy == "direct_system":
            dns["direct-nameserver"] = ["system"]
            dns["direct-nameserver-follow-policy"] = False
        config = {"mixed-port": proxy_port, "external-controller": f"127.0.0.1:{controller_port}",
                  "mode": "rule", "log-level": "warning", "tun": {"enable": False},
                  "dns": dns, "rules": ["DOMAIN,copilot.tencent.com,DIRECT", "MATCH,DIRECT"]}
        path = root / "config.yaml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        env = {key: os.environ[key] for key in ("SystemRoot", "PATH", "TEMP", "TMP") if key in os.environ}
        process = subprocess.Popen([executable, "-d", str(root), "-f", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        result = {"policy": policy, "fixture_dns": bool(fixture_port), "owned_pid": process.pid,
                  "proxy_port": proxy_port, "controller_port": controller_port}
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{controller_port}", trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 10
                while True:
                    assert process.poll() is None, "owned Mihomo exited during startup"
                    try:
                        if client.get("/version").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert time.monotonic() < deadline, "owned controller did not start"
                    time.sleep(.05)
                response = client.get("/dns/query", params={"name": "copilot.tencent.com", "type": "A"}, timeout=10)
                response.raise_for_status()
                result["answers"] = response.json().get("Answer")
            result["head"] = []
            with httpx.Client(proxy=f"http://127.0.0.1:{proxy_port}", trust_env=False, timeout=5) as client:
                for _ in range(2):
                    start = time.monotonic()
                    try:
                        response = client.head("https://copilot.tencent.com/")
                        sample = {"status": response.status_code}
                    except httpx.HTTPError as error:
                        sample = {"failure": type(error).__name__}
                    sample["elapsed_ms"] = round(1000 * (time.monotonic() - start), 1)
                    result["head"].append(sample)
        finally:
            process.terminate()
            process.wait(timeout=5)
            result["retired"] = process.poll() is not None
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mihomo", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fixture", action="store_true", help="Prove DIRECT re-resolution despite deliberately wrong initial DNS")
    args = parser.parse_args()
    if args.fixture:
        with DNSFixture() as fixture:
            results = [probe(args.mihomo, policy, fixture.port) for policy in ("default", "direct_system")]
        assert all(r["retired"] for r in results)
        assert all(sample.get("failure") for sample in results[0]["head"])
        assert all(sample.get("status") == 404 for sample in results[1]["head"])
    else:
        results = [probe(args.mihomo, policy) for policy in ("default", "domain_system", "direct_system")]
    data = json.dumps(results, ensure_ascii=True, indent=2)
    Path(args.output).write_text(data + "\n", encoding="utf-8")
    print(data)
