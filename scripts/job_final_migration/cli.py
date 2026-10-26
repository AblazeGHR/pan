"""Read-only inventory and explicit transactional offline apply CLI."""

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
            "Inventory/dry-run by default; --apply publishes a disabled canonical "
            "bundle outside --root. Never starts services or writes source data."
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
    parser.add_argument("--target", type=Path, help="offline canonical bundle JSON, outside root")
    parser.add_argument("--context", type=Path, help="explicit pinned references and operator attestations JSON")
    parser.add_argument("--user-authorization", type=Path, help="independently supplied user instruction receipt JSON")
    parser.add_argument("--expected-user-authorization-digest", help="externally pinned user receipt digest; not read from context")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="atomically publish the entire offline bundle")
    mode.add_argument("--rollback", action="store_true", help="roll back offline publication; never restore/run legacy")
    mode.add_argument("--dry-run", action="store_true", help="explicit default: validate without target writes")
    parser.add_argument("--expected-digest", help="reviewed planDigest (apply) or bundleDigest (rollback)")
    parser.add_argument("--mark-read-only", action="store_true", help="record old-record read-only intent in committed bundle")
    parser.add_argument("--preserve-incompatible", action="store_true",
                        help="reviewed partial apply: preserve complex/unknown bytes, import selected simple only")
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
        from scripts.job_final_migration.apply import _atomic_write, outside_root
        destination = outside_root(root, destination)
        _atomic_write(destination, content.encode("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        if report.get("migrationExecuted") or report.get("state") == "rolled-back-offline":
            print(f"offline operation already committed: {report['operationId']}; inspect the original target",
                  file=sys.stderr)
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
    # Validate every destination before any migration side effect.
    try:
        from scripts.job_final_migration.apply import outside_root
        destination = outside_root(root, args.out) if args.out != "-" else None
        target = outside_root(root, args.target) if args.target else None
        for path in (destination, target):
            if path and path.exists() and not path.is_file():
                raise ValueError("output/target must be a file path")
            if path:
                ancestor = next(p for p in path.parents if p.exists())
                if not ancestor.is_dir():
                    raise ValueError("output/target parent is not a directory")
        if destination and target and destination in {
                target, target.with_name(target.name + ".bak"), target.with_name(target.name + ".lock")}:
            raise ValueError("report output collides with migration target/backup/lock")
        if destination and destination.exists() and destination.stat().st_nlink > 1:
            raise ValueError("report output has multiple hard links")
        if (args.apply or args.rollback or args.context or args.mark_read_only or args.preserve_incompatible) and not target:
            raise ValueError("--target required for apply/context/rollback/read-only marker")
        if args.mark_read_only and not args.apply:
            raise ValueError("--mark-read-only requires --apply")
        if target and not args.rollback and not args.context:
            raise ValueError("--context required with --target")
        if args.rollback and (args.context or args.mark_read_only or args.preserve_incompatible):
            raise ValueError("rollback does not accept context/read-only marker")
        if bool(args.user_authorization) != bool(args.expected_user_authorization_digest):
            raise ValueError("user authorization requires both receipt and externally pinned digest")
        if args.user_authorization and (not target or args.rollback):
            raise ValueError("user authorization requires context/target and cannot be used with rollback")
        for source in (args.context, args.user_authorization):
            if source and source.resolve() in {destination, target}:
                raise ValueError("output/target collides with context/authorization input")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        report = build_inventory(
            root, allow_real_profile=args.allow_real_profile)
        if target:
            from scripts.job_final_migration.apply import migrate, rollback
            from packages.jobs.final_contracts import loads
            if args.rollback:
                report = rollback(root, target, expected_digest=args.expected_digest,
                                  allow_real_profile=args.allow_real_profile)
            else:
                context = loads(args.context.read_text(encoding="utf-8-sig"))
                report = migrate(root, target, context, apply=args.apply,
                                 mark_read_only=args.mark_read_only,
                                 expected_digest=args.expected_digest,
                                 allow_real_profile=args.allow_real_profile,
                                 preserve_incompatible=args.preserve_incompatible,
                                 user_authorization=loads(args.user_authorization.read_text(encoding="utf-8-sig"))
                                                    if args.user_authorization else None,
                                 expected_user_authorization_digest=args.expected_user_authorization_digest)
        else:
            from packages.jobs.final_contracts import digest
            report["inventoryDigest"] = digest(report)
    except RealProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "No file was read from the candidate root and no output was written. "
            "Re-run with --allow-real-profile only after explicit authorization.",
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: inventory failed: {exc}", file=sys.stderr)
        return 1
    if args.rollback:
        if args.format != "json":
            print("rollback: offline publication rolled back", file=sys.stderr)
            args.format = "json"
        return _write_output(report, args.out, args.format, root)
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
            "warning: real-profile marker accepted; source root remains read-only",
            file=sys.stderr,
        )
    return _write_output(report, args.out, args.format, root)


if __name__ == "__main__":
    raise SystemExit(main())
