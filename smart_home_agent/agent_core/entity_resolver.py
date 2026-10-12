from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from smart_home_agent.services.access_control import mask_user_id, normalized_text


@dataclass(frozen=True)
class ResolvedUser:
    channel: str
    user_id: str
    display_name: str | None
    username: str | None
    role: str
    enabled: bool


@dataclass(frozen=True)
class UserResolution:
    status: str
    matches: list[ResolvedUser]

    @property
    def single(self) -> ResolvedUser | None:
        return self.matches[0] if self.status == "resolved" else None


class EntityResolver:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def resolve_user(self, query: str | None, *, prefer_blocked: bool = False) -> UserResolution:
        normalized_query = normalized_text(query or "").lstrip("@")
        if not normalized_query:
            return UserResolution("not_found", [])

        rows = self.conn.execute(
            """
            SELECT channel, user_id, display_name, username, role, enabled
            FROM users
            ORDER BY enabled DESC, role, channel, user_id
            """
        ).fetchall()
        exact: list[sqlite3.Row] = []
        contains: list[sqlite3.Row] = []
        for row in rows:
            fields = [
                row["user_id"] or "",
                row["display_name"] or "",
                row["username"] or "",
                f"{row['channel']} {row['user_id']}",
            ]
            normalized_fields = [normalized_text(field).lstrip("@") for field in fields]
            if normalized_query in normalized_fields:
                exact.append(row)
            elif any(normalized_query in field for field in normalized_fields if field):
                contains.append(row)

        candidates = exact or contains
        if prefer_blocked:
            blocked = [row for row in candidates if row["role"] == "blocked" or not bool(row["enabled"])]
            candidates = blocked or candidates

        matches = [
            ResolvedUser(
                channel=row["channel"],
                user_id=row["user_id"],
                display_name=row["display_name"],
                username=row["username"],
                role=row["role"],
                enabled=bool(row["enabled"]),
            )
            for row in candidates
        ]
        if not matches:
            return UserResolution("not_found", [])
        if len(matches) > 1:
            return UserResolution("ambiguous", matches[:5])
        return UserResolution("resolved", matches)


def format_user_options(matches: list[ResolvedUser]) -> str:
    return "; ".join(
        f"{row.display_name or row.username or row.channel} ({row.channel} {mask_user_id(row.user_id)}, role={row.role})"
        for row in matches
    )
