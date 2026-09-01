"""Create the first platform administrator without storing a default secret.

Run after ``alembic upgrade head`` from the backend directory:
    python scripts/create_admin.py
"""

from __future__ import annotations

import argparse
import getpass
import sys
from uuid import uuid4
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.domains.auth.models import User, UserRole
from app.domains.auth.service import PASSWORD_HASHER, USER_ROLE, normalize_email, validate_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a GapMind platform administrator")
    parser.add_argument("--email", help="Administrator email; prompted when omitted")
    parser.add_argument("--display-name", default="平台管理员")
    args = parser.parse_args()

    email = args.email or input("管理员邮箱: ").strip()
    normalized = normalize_email(email)
    password = getpass.getpass("管理员密码: ")
    password_again = getpass.getpass("再次输入密码: ")
    if password != password_again:
        raise SystemExit("两次密码不一致")
    validate_password(password)

    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email_normalized == normalized))
        if existing is not None:
            raise SystemExit("该邮箱已经存在账号；请使用管理员后台或数据库运维流程处理角色")
        user = User(
            id=str(uuid4()),
            email=email,
            email_normalized=normalized,
            display_name=args.display_name.strip()[:128] or "平台管理员",
            account_type="human",
            status="active",
            password_hash=PASSWORD_HASHER.hash(password),
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role=USER_ROLE))
        db.add(UserRole(user_id=user.id, role="platform_admin"))
        db.commit()
        print(f"已创建平台管理员: {user.email}")


if __name__ == "__main__":
    main()
