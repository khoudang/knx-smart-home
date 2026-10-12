from __future__ import annotations

import argparse
import os
from pathlib import Path

from smart_home_agent.services.access_control import (
    approve_request,
    expire_guests,
    mask_user_id,
    reject_request,
    revoke_user,
    set_user_enabled,
    update_role as update_user_role,
)
from smart_home_agent.services.user_repository import VALID_ROLES, resolve_user_role, upsert_user
from smart_home_agent.storage.sqlite import connect, migrate


VALID_CHANNELS = {"telegram", "zalo", "app", "voice", "homebridge"}


def database_path(value: str | None) -> Path:
    raw = value or os.environ.get("DATABASE_PATH")
    if not raw:
        raise SystemExit("Database path is required. Pass --database or set DATABASE_PATH.")
    return Path(raw)


def open_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    migrate(conn)
    return conn


def validate_channel(channel: str) -> str:
    if channel not in VALID_CHANNELS:
        raise argparse.ArgumentTypeError(f"channel must be one of: {', '.join(sorted(VALID_CHANNELS))}")
    return channel


def validate_role(role: str) -> str:
    if role not in VALID_ROLES:
        raise argparse.ArgumentTypeError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
    return role


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage SQLite users for smart-home-agent.")
    parser.add_argument("--database", help="SQLite database path. Required unless DATABASE_PATH is set.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list")

    add = sub.add_parser("add")
    add.add_argument("--channel", required=True, type=validate_channel)
    add.add_argument("--user-id", required=True)
    add.add_argument("--display-name")
    add.add_argument("--role", required=True, type=validate_role)

    update = sub.add_parser("update-role")
    update.add_argument("--channel", required=True, type=validate_channel)
    update.add_argument("--user-id", required=True)
    update.add_argument("--role", required=True, type=validate_role)
    update.add_argument("--actor-channel", default="cli")
    update.add_argument("--actor-user-id", default="cli")

    disable = sub.add_parser("disable")
    disable.add_argument("--channel", required=True, type=validate_channel)
    disable.add_argument("--user-id", required=True)
    disable.add_argument("--actor-channel", default="cli")
    disable.add_argument("--actor-user-id", default="cli")

    enable = sub.add_parser("enable")
    enable.add_argument("--channel", required=True, type=validate_channel)
    enable.add_argument("--user-id", required=True)
    enable.add_argument("--actor-channel", default="cli")
    enable.add_argument("--actor-user-id", default="cli")

    revoke = sub.add_parser("revoke")
    revoke.add_argument("--channel", required=True, type=validate_channel)
    revoke.add_argument("--user-id", required=True)
    revoke.add_argument("--actor-channel", default="cli")
    revoke.add_argument("--actor-user-id", default="cli")

    sub.add_parser("list-requests")

    approve = sub.add_parser("approve-request")
    approve.add_argument("--request-id", required=True, type=int)
    approve.add_argument("--role", required=True, type=validate_role)
    approve.add_argument("--guest-days", type=int)
    approve.add_argument("--actor-channel", default="cli")
    approve.add_argument("--actor-user-id", default="cli")

    reject = sub.add_parser("reject-request")
    reject.add_argument("--request-id", required=True, type=int)
    reject.add_argument("--reason")
    reject.add_argument("--actor-channel", default="cli")
    reject.add_argument("--actor-user-id", default="cli")

    sub.add_parser("expire-guests")

    args = parser.parse_args()
    db_path = database_path(args.database)
    conn = open_db(db_path)
    try:
        if args.command == "list":
            rows = conn.execute(
                "SELECT channel, user_id, display_name, role, enabled, expires_at FROM users ORDER BY channel, user_id"
            ).fetchall()
            for row in rows:
                enabled = "enabled" if row["enabled"] else "disabled"
                name = row["display_name"] or ""
                expires = row["expires_at"] or ""
                print(f"{row['channel']}\t{row['user_id']}\t{name}\t{row['role']}\t{enabled}\t{expires}")
            if not rows:
                print("No users configured.")
        elif args.command == "add":
            upsert_user(
                conn,
                channel=args.channel,
                user_id=args.user_id,
                display_name=args.display_name,
                role=args.role,
                enabled=True,
            )
            print(f"[OK] Upserted {args.channel}:{args.user_id} as {args.role}")
        elif args.command == "update-role":
            current = resolve_user_role(conn, args.channel, args.user_id)
            if not current.known:
                raise SystemExit("User not found; use add first.")
            print(
                update_user_role(
                    conn,
                    actor_channel=args.actor_channel,
                    actor_user_id=args.actor_user_id,
                    channel=args.channel,
                    user_id=args.user_id,
                    role=args.role,
                )
            )
        elif args.command == "disable":
            print(
                set_user_enabled(
                    conn,
                    actor_channel=args.actor_channel,
                    actor_user_id=args.actor_user_id,
                    channel=args.channel,
                    user_id=args.user_id,
                    enabled=False,
                )
            )
        elif args.command == "enable":
            print(
                set_user_enabled(
                    conn,
                    actor_channel=args.actor_channel,
                    actor_user_id=args.actor_user_id,
                    channel=args.channel,
                    user_id=args.user_id,
                    enabled=True,
                )
            )
        elif args.command == "revoke":
            print(
                revoke_user(
                    conn,
                    actor_channel=args.actor_channel,
                    actor_user_id=args.actor_user_id,
                    channel=args.channel,
                    user_id=args.user_id,
                )
            )
        elif args.command == "list-requests":
            rows = conn.execute(
                """
                SELECT id, channel, user_id, display_name, requested_role, status, created_at
                FROM access_requests
                ORDER BY id DESC
                LIMIT 50
                """
            ).fetchall()
            for row in rows:
                name = row["display_name"] or ""
                print(
                    f"#{row['id']}\t{row['channel']}\t{mask_user_id(row['user_id'])}\t{name}\t"
                    f"{row['requested_role']}\t{row['status']}\t{row['created_at']}"
                )
            if not rows:
                print("No access requests.")
        elif args.command == "approve-request":
            print(
                approve_request(
                    conn,
                    request_id=args.request_id,
                    actor_channel=args.actor_channel,
                    actor_user_id=args.actor_user_id,
                    role=args.role,
                    guest_days=args.guest_days,
                )
            )
        elif args.command == "reject-request":
            print(
                reject_request(
                    conn,
                    request_id=args.request_id,
                    actor_channel=args.actor_channel,
                    actor_user_id=args.actor_user_id,
                    reason=args.reason,
                )
            )
        elif args.command == "expire-guests":
            print(f"[OK] Expired guests: {expire_guests(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
