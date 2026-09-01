"""Transfer historical workspaces from the legacy system owner to an admin.

This is an explicit, one-time data migration for the pre-authentication data
set. It changes only ``workspaces.owner_id``; workspace-scoped papers,
artifacts, knowledge, and other records are preserved in place.

The command is read-only by default. Pass ``--apply`` to commit the transfer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, update

from app.db.session import SessionLocal
from app.domains.auth.models import User, UserRole
from app.domains.workspace.models import Workspace

LEGACY_OWNER_EMAIL = "legacy-owner@system.gapmind"
ADMIN_EMAIL = "yuanxing629@163.com"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transfer legacy Workspace ownership to the platform admin."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the ownership transfer; without it, only print the plan",
    )
    return parser.parse_args()


def _workspace_counts(db, owner_id: str) -> tuple[int, int, int]:
    total = db.scalar(
        select(func.count()).select_from(Workspace).where(Workspace.owner_id == owner_id)
    )
    active = db.scalar(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.owner_id == owner_id, Workspace.is_deleted.is_(False))
    )
    deleted = db.scalar(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.owner_id == owner_id, Workspace.is_deleted.is_(True))
    )
    return int(total or 0), int(active or 0), int(deleted or 0)


def main() -> int:
    args = _parse_args()
    db = SessionLocal()
    try:
        source = db.scalar(
            select(User).where(User.email_normalized == LEGACY_OWNER_EMAIL)
        )
        target = db.scalar(select(User).where(User.email_normalized == ADMIN_EMAIL))
        if source is None:
            raise RuntimeError(f"source user not found: {LEGACY_OWNER_EMAIL}")
        if target is None:
            raise RuntimeError(f"target admin not found: {ADMIN_EMAIL}")
        if source.account_type != "system":
            raise RuntimeError(
                f"unexpected source account_type for {source.email}: {source.account_type}"
            )
        if target.status != "active":
            raise RuntimeError(f"target admin is not active: {target.email}")

        roles = set(
            db.scalars(select(UserRole.role).where(UserRole.user_id == target.id)).all()
        )
        if "platform_admin" not in roles:
            raise RuntimeError(f"target user is not a platform admin: {target.email}")

        total, active, deleted = _workspace_counts(db, source.id)
        print(f"source={source.email} ({source.id})")
        print(f"target={target.email} ({target.id})")
        print(f"workspaces_total={total} active={active} soft_deleted={deleted}")

        if not args.apply:
            print("dry-run: no database changes made; pass --apply to commit")
            return 0

        result = db.execute(
            update(Workspace)
            .where(Workspace.owner_id == source.id)
            .values(owner_id=target.id)
        )
        db.commit()
        print(f"transferred={result.rowcount}")

        remaining = _workspace_counts(db, source.id)[0]
        target_total, target_active, target_deleted = _workspace_counts(db, target.id)
        if remaining != 0 or target_total < total:
            raise RuntimeError(
                "post-migration verification failed: source or target counts are inconsistent"
            )
        print(
            "verified: "
            f"source_remaining={remaining}; "
            f"target_total={target_total} active={target_active} "
            f"soft_deleted={target_deleted}"
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
