from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from smart_home_agent.services.device_sync import sync_devices
from smart_home_agent.storage.sqlite import connect, migrate


def load_fixture(path: Path) -> dict[str, list[dict[str, Any]]]:
    return json.loads(path.read_text(encoding="utf-8"))


def transform_devices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transformed = []
    for row in rows:
        transformed.append(
            {
                "device_id": row.get("id") or row.get("device_id"),
                "Ten line den": row.get("name") or row.get("id") or row.get("device_id"),
                "Khu vuc": row.get("room"),
                "device_type": row.get("type") or row.get("device_type") or "light",
                "G.A Control": row.get("group_address"),
                "capabilities": row.get("capabilities") or ["turn_on", "turn_off", "read_state"],
            }
        )
    return transformed


def transform_users(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    users = []
    for row in rows:
        platform = row.get("platform") or row.get("channel") or "unknown"
        role = row.get("role") or "family"
        if role == "user":
            role = "family"
        if role not in {"owner", "family", "guest", "installer"}:
            role = "guest"
        users.append(
            {
                "channel": platform,
                "user_id": row.get("user_id"),
                "role": role,
                "enabled": row.get("enabled", 1),
            }
        )
    return users


def import_fixture_to_sqlite(
    fixture: dict[str, list[dict[str, Any]]],
    sqlite_path: Path,
    execute: bool,
    include_users: bool = False,
) -> dict[str, int]:
    devices = transform_devices(fixture.get("devices", []))
    users = transform_users(fixture.get("users_permissions", [])) if include_users else []
    counts = {"devices_seen": len(devices), "devices_imported": 0, "users_seen": len(users), "users_imported": 0}
    if not execute:
        return counts

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(sqlite_path)
    try:
        migrate(conn)
        result = sync_devices(conn, devices)
        counts["devices_imported"] = result.inserted + result.updated + result.unchanged
        with conn:
            for user in users:
                conn.execute(
                    """
                    INSERT INTO users (channel, user_id, role, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(channel, user_id) DO UPDATE SET
                        role = excluded.role,
                        enabled = excluded.enabled,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (user["channel"], user["user_id"], user["role"], int(bool(user["enabled"]))),
                )
        counts["users_imported"] = len(users)
        return counts
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Postgres-exported fixture into SQLite. Dry-run by default.")
    parser.add_argument("--fixture", required=True, help="JSON fixture exported from Postgres")
    parser.add_argument("--sqlite", default="data/smart_home.db", help="SQLite target path")
    parser.add_argument("--execute", action="store_true", help="Actually write SQLite")
    parser.add_argument("--include-users", action="store_true", help="Import users_permissions into SQLite users")
    args = parser.parse_args()

    fixture = load_fixture(Path(args.fixture))
    counts = import_fixture_to_sqlite(fixture, Path(args.sqlite), args.execute, include_users=args.include_users)
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(json.dumps({"mode": mode, "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
