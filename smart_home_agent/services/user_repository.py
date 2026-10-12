from __future__ import annotations

import sqlite3
from dataclasses import dataclass


VALID_ROLES = {"owner", "admin", "member", "viewer", "guest", "blocked"}
LEGACY_ROLE_ALIASES = {
    "family": "member",
    "installer": "admin",
}


@dataclass(frozen=True)
class UserRole:
    user_id: str
    channel: str
    role: str
    enabled: bool
    known: bool
    expires_at: str | None = None


def resolve_user_role(conn: sqlite3.Connection, channel: str, user_id: str) -> UserRole:
    row = conn.execute(
        """
        SELECT user_id, channel, role, enabled, expires_at
        FROM users
        WHERE channel = ? AND user_id = ?
        """,
        (channel, user_id),
    ).fetchone()
    if not row:
        return UserRole(user_id=user_id, channel=channel, role="guest", enabled=True, known=False)
    role = normalize_role(row["role"])
    return UserRole(
        user_id=row["user_id"],
        channel=row["channel"],
        role=role,
        enabled=bool(row["enabled"]),
        known=True,
        expires_at=row["expires_at"] if "expires_at" in row.keys() else None,
    )


def upsert_user(
    conn: sqlite3.Connection,
    *,
    channel: str,
    user_id: str,
    role: str = "family",
    display_name: str | None = None,
    username: str | None = None,
    enabled: bool = True,
    expires_at: str | None = None,
) -> None:
    role = normalize_role(role)
    if role not in VALID_ROLES:
        raise ValueError(f"unsupported role: {role}")
    with conn:
        conn.execute(
            """
            INSERT INTO users (channel, user_id, display_name, username, role, enabled, expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(channel, user_id) DO UPDATE SET
                display_name = COALESCE(NULLIF(excluded.display_name, ''), users.display_name),
                username = COALESCE(NULLIF(excluded.username, ''), users.username),
                role = excluded.role,
                enabled = excluded.enabled,
                expires_at = excluded.expires_at,
                updated_at = CURRENT_TIMESTAMP
            """,
            (channel, user_id, display_name, username, role, int(enabled), expires_at),
        )


def normalize_role(role: str | None) -> str:
    raw = (role or "guest").strip().lower()
    return LEGACY_ROLE_ALIASES.get(raw, raw)
