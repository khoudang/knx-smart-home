from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class ConversationState:
    channel: str
    user_id: str
    state: str
    payload: dict
    expires_at: str
    consumed: bool = False
    source_message_id: str | None = None


def save_context(
    conn: sqlite3.Connection,
    *,
    channel: str,
    user_id: str,
    state: str,
    payload: dict,
    ttl_seconds: int = 300,
    source_message_id: str | None = None,
) -> None:
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    body = {
        "state": state,
        "payload": payload,
        "consumed": False,
        "source_message_id": source_message_id,
    }
    conn.execute(
        """
        INSERT INTO conversation_context (channel, user_id, context_json, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (channel, user_id, json.dumps(body, ensure_ascii=False), expires.isoformat()),
    )


def get_active_context(conn: sqlite3.Connection, *, channel: str, user_id: str) -> ConversationState | None:
    row = conn.execute(
        """
        SELECT id, context_json, expires_at
        FROM conversation_context
        WHERE channel = ? AND user_id = ? AND expires_at > ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel, user_id, datetime.now(timezone.utc).isoformat()),
    ).fetchone()
    if not row:
        return None
    data = json.loads(row["context_json"] or "{}")
    if data.get("consumed"):
        return None
    return ConversationState(
        channel=channel,
        user_id=user_id,
        state=str(data.get("state") or ""),
        payload=data.get("payload") or {},
        expires_at=row["expires_at"],
        consumed=False,
        source_message_id=data.get("source_message_id"),
    )


def consume_context(conn: sqlite3.Connection, *, channel: str, user_id: str) -> None:
    row = conn.execute(
        """
        SELECT id, context_json
        FROM conversation_context
        WHERE channel = ? AND user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel, user_id),
    ).fetchone()
    if not row:
        return
    data = json.loads(row["context_json"] or "{}")
    data["consumed"] = True
    conn.execute(
        "UPDATE conversation_context SET context_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), row["id"]),
    )
