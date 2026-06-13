"""GDPR data deletion CLI.

Supports two operations:
  1. Delete all data for a specific user (right-to-erasure request):
       python src/gdpr_purge.py --user-oid <entra-object-id>

  2. Bulk-delete audit events older than N days (retention enforcement):
       python src/gdpr_purge.py --older-than-days 90

Both operations write a deletion receipt to stdout so the operator can log it
for compliance evidence. The exit code is 0 on success, 1 on error.

This script is intentionally separate from the main app so it can be run by
a compliance officer or a scheduled job without starting the Streamlit UI.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from the repo root or from src/.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from memory.audit_log import AuditLog  # noqa: E402


def _purge_user(user_oid: str, dry_run: bool) -> int:
    if dry_run:
        print(f"[DRY RUN] Would delete all audit data for user OID: {user_oid}")
        return 0
    deleted = AuditLog.purge_user_data(user_oid)
    print(
        f"GDPR erasure complete — user OID: {user_oid} | "
        f"audit rows deleted: {deleted} | "
        f"timestamp: {datetime.now(timezone.utc).isoformat()}"
    )
    return deleted


def _purge_old(days: int, dry_run: bool) -> int:
    if dry_run:
        print(f"[DRY RUN] Would delete audit rows older than {days} day(s)")
        return 0
    deleted = AuditLog.purge_old_runs(retention_days=days)
    print(
        f"Retention purge complete — rows older than {days}d deleted: {deleted} | "
        f"timestamp: {datetime.now(timezone.utc).isoformat()}"
    )
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="GDPR-compliant data deletion for Backlog Synthesizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--user-oid",
        metavar="OID",
        help="Entra ID object ID (oid claim) — delete ALL data for this user",
    )
    group.add_argument(
        "--older-than-days",
        type=int,
        metavar="N",
        help="Delete audit rows older than N days (retention enforcement)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without making any changes",
    )
    args = parser.parse_args()

    try:
        if args.user_oid:
            _purge_user(args.user_oid, args.dry_run)
        else:
            if args.older_than_days <= 0:
                print("ERROR: --older-than-days must be a positive integer", file=sys.stderr)
                return 1
            _purge_old(args.older_than_days, args.dry_run)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
