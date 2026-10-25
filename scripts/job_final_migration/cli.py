"""CLI for the read-only Job final-model migration inventory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Allow ``python scripts/job_final_migration/cli.py`` from a checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.job_final_migration.inventory import (  # type: ignore
        RealProfileError, build_inventory)
    from scripts.job_final_migration.render import (  # type: ignore
        render_json, render_markdown)
else:
    from .inventory import RealProfileError, build_inventory
    from .render import render_json, render_markdown


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.job_final_migration",
        description=(
            "Read-only inventory and dry-run plan for old Job/Scheduler "
            "records. This command never migrates, writes to the scanned root, "
            "starts a service, or touches a real profile by default."
        ),
    )
    parser.add_argument(
        "--root", type=Path, required=True,
        help="explicit Pan data root to scan (no default)",
    )
    parser.add_argument(
        "--out", required=True,
        help="output file, or - for stdout",
    )
    parser.add_argument(
        "--format", choices=("json", "md"), default="json",
        help="output format (default: json)",
    )
    parser.add_argument(
        "--allow-real-profile", action="store_true",
        help=(
            "allow reading a root that contains process.json; the scan remains "
            "read-only and output is still refused inside the root"
        ),
    )
    return parser


def _write_output(report: dict, out: str, fmt: str, root: Path) -> int:
    if fmt == "md":
        content = render_markdown(report)
    else:
        content = render_json(report)
    if out == "-":
        sys.stdout.write(content)
        return 0
    destination = Path(out).expanduser()
    try:
        destination = destination.resolve(strict=False)
    except OSError as exc:
        print(f"error: cannot resolve output path: {exc}", file=sys.stderr)
        return 2
    try:
        destination.relative_to(root)
    except ValueError:
        pass
    else:
        print(
            "error: refusing to write output inside the scanned root; "
            "choose a path outside it or use --out -",
            file=sys.stderr,
        )
        return 2
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.expanduser()
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        print(f"error: root does not exist: {exc}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"error: root is not a directory: {root}", file=sys.stderr)
        return 2
    try:
        report = build_inventory(
            root, allow_real_profile=args.allow_real_profile)
    except RealProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "No file was read from the candidate root and no output was written. "
            "Re-run with --allow-real-profile only after explicit authorization.",
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: inventory failed: {exc}", file=sys.stderr)
        return 1
    summary = report["summary"]
    print(
        "inventory: "
        f"total={summary['total']} simple={summary['simple']} "
        f"complex={summary['complex']} unknown={summary['unknown']} "
        f"scanErrors={summary['scanErrors']}",
        file=sys.stderr,
    )
    if report["root"]["realProfile"]:
        print(
            "warning: real-profile marker accepted; this scan is still read-only",
            file=sys.stderr,
        )
    return _write_output(report, args.out, args.format, root)


if __name__ == "__main__":
    raise SystemExit(main())
