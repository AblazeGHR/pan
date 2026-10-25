"""Argument parsing and JSON presentation for the shared canonical client."""
import json
import os
from pathlib import Path
import sys

from . import final_contracts as contracts
from .final_client import CanonicalClient, HTTPTransport, RESOURCES, RUN_OPERATIONS


def add_parser(sub):
    final = sub.add_parser("final", help="Canonical Job clients (/api/final); no service lifecycle actions")
    final.add_argument("--api-url", help="HTTP(S) origin; default PAN_API_URL or checkout port")
    groups = final.add_subparsers(dest="resource", required=True)
    for resource in (*RESOURCES, "job-runs", "operations", "actions"):
        group = groups.add_parser(resource)
        operations = group.add_subparsers(dest="operation", required=True)
        names = (["list", "get", "create", "update", "retire", "control"] if resource in RESOURCES
                 else ["get", *RUN_OPERATIONS] if resource == "job-runs"
                 else ["get", "find"] if resource == "operations" else ["list"])
        if resource == "jobs":
            names += ["runs", "run"]
        for name in names:
            item = operations.add_parser(name)
            if name not in {"list", "create", "find"}:
                item.add_argument("object_id")
            if name in {"list", "runs"} and resource != "actions":
                item.add_argument("--cursor")
                item.add_argument("--limit", type=int, default=100)
            if name not in {"list", "runs", "get"}:
                item.add_argument("--file", required=True, help="WP01 JSON request body file; '-' reads stdin")


def run(args, root):
    from packages.core import launcher
    try:
        url = args.api_url or os.environ.get("PAN_API_URL")
        if not url:
            url = f"http://127.0.0.1:{launcher.configured_port(root, environ={})}"
        client = CanonicalClient(HTTPTransport(url, os.environ.get("PAN_API_TOKEN")))
        body = None
        if hasattr(args, "file"):
            raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
            if len(raw.encode("utf-8")) > contracts.MAX_DEFINITION_BYTES:
                raise contracts.ContractError("invalid_schema", "Request exceeds contract byte limit")
            body = contracts.loads(raw)
        result = client.call(args.resource, args.operation, object_id=getattr(args, "object_id", None),
                             body=body, cursor=getattr(args, "cursor", None), limit=getattr(args, "limit", 100))
    except contracts.ContractError as exc:
        result = {"ok": False, "error": exc.as_dict()}
    except (OSError, ValueError) as exc:
        result = {"ok": False, "error": {"code": "client_error", "message": str(exc)}}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("ok") is False or "error" in result or result.get("status") in {"rejected", "unknown"} else 0
